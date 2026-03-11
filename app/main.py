import json
import time
import uuid
import asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select, delete

from app.database import Base, create_db_engine, create_session_factory
from app.models import Session, Line, Canvas
from app.names import generate_name

STATIC_DIR = Path(__file__).parent.parent / "static"
SESSION_EXPIRY_DAYS = 30
RATE_LIMIT_LINES = 100       # max lines per window
RATE_LIMIT_WINDOW = 60       # seconds
ACTIVITY_TIMEOUT = 10        # seconds before a user is considered inactive

# Module-level engine/session — can be overridden by tests
engine = create_db_engine()
async_session = create_session_factory(engine)


# ── Connection manager ──────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        # session_id -> WebSocket
        self.active: dict[uuid.UUID, WebSocket] = {}
        # session_id -> {name, color, x, y}
        self.cursors: dict[uuid.UUID, dict] = {}
        # session_id -> last activity timestamp (time.monotonic())
        self.last_activity: dict[uuid.UUID, float] = {}
        # Rate limiting: session_id -> list of timestamps for draw_end events
        self.line_timestamps: dict[uuid.UUID, list[float]] = defaultdict(list)
        # session_id -> canvas_hash (which canvas the user is on)
        self.canvas_map: dict[uuid.UUID, str] = {}

    async def connect(self, session_id: uuid.UUID, ws: WebSocket, name: str, color: str, canvas_hash: str):
        await ws.accept()
        self.active[session_id] = ws
        self.cursors[session_id] = {"name": name, "color": color, "x": 0, "y": 0}
        self.last_activity[session_id] = time.monotonic()
        self.canvas_map[session_id] = canvas_hash

    def disconnect(self, session_id: uuid.UUID):
        self.active.pop(session_id, None)
        self.cursors.pop(session_id, None)
        self.last_activity.pop(session_id, None)
        self.line_timestamps.pop(session_id, None)
        self.canvas_map.pop(session_id, None)

    def touch_activity(self, session_id: uuid.UUID):
        """Mark a user as recently active."""
        self.last_activity[session_id] = time.monotonic()

    def check_rate_limit(self, session_id: uuid.UUID) -> tuple[bool, int]:
        """Check if user can draw another line. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        window_start = now - RATE_LIMIT_WINDOW
        # Prune old timestamps
        timestamps = self.line_timestamps[session_id]
        self.line_timestamps[session_id] = [t for t in timestamps if t > window_start]
        timestamps = self.line_timestamps[session_id]

        if len(timestamps) >= RATE_LIMIT_LINES:
            # How long until the oldest entry in the window expires
            retry_after = int(timestamps[0] - window_start) + 1
            return False, retry_after

        return True, 0

    def record_line(self, session_id: uuid.UUID):
        """Record a line creation for rate limiting."""
        self.line_timestamps[session_id].append(time.monotonic())

    def _canvas_peers(self, canvas_hash: str) -> dict[uuid.UUID, WebSocket]:
        """Return active connections on the same canvas."""
        return {
            sid: ws for sid, ws in self.active.items()
            if self.canvas_map.get(sid) == canvas_hash
        }

    async def broadcast(self, message: dict, exclude: uuid.UUID | None = None, canvas_hash: str | None = None):
        data = json.dumps(message)
        dead = []
        peers = self._canvas_peers(canvas_hash) if canvas_hash else self.active
        for sid, ws in peers.items():
            if sid == exclude:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.disconnect(sid)

    def active_users(self, canvas_hash: str | None = None) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "session_id": str(sid),
                "last_active": now - self.last_activity.get(sid, now),
                **info,
            }
            for sid, info in self.cursors.items()
            if canvas_hash is None or self.canvas_map.get(sid) == canvas_hash
        ]


manager = ConnectionManager()


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # cleanup expired sessions on startup
    async with async_session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SESSION_EXPIRY_DAYS)
        expired = await db.execute(
            select(Session.id).where(Session.last_seen < cutoff)
        )
        expired_ids = [row[0] for row in expired.all()]
        if expired_ids:
            await db.execute(delete(Line).where(Line.session_id.in_(expired_ids)))
            await db.execute(delete(Session).where(Session.id.in_(expired_ids)))
            await db.commit()
    yield


app = FastAPI(lifespan=lifespan)


# ── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/canvas/{canvas_hash}")
async def canvas_page(canvas_hash: str):
    """Serve the same SPA for any canvas URL — the JS reads the hash from the URL."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/session")
async def get_or_create_session(session_id: str | None = Query(None)):
    """Return existing session or create a new one."""
    async with async_session() as db:
        if session_id:
            try:
                sid = uuid.UUID(session_id)
            except ValueError:
                sid = None
            if sid:
                result = await db.execute(select(Session).where(Session.id == sid))
                session = result.scalar_one_or_none()
                if session:
                    session.last_seen = datetime.now(timezone.utc)
                    await db.commit()
                    return {
                        "session_id": str(session.id),
                        "name": session.name,
                        "color": session.color,
                    }

        # Create new session
        taken_result = await db.execute(select(Session.name))
        taken_names = {row[0] for row in taken_result.all()}
        name, color = generate_name(taken_names)

        session = Session(name=name, color=color)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return {
            "session_id": str(session.id),
            "name": session.name,
            "color": session.color,
        }


@app.post("/api/canvas")
async def create_canvas(session_id: str = Query(...)):
    """Create a new canvas owned by the given session."""
    sid = uuid.UUID(session_id)
    async with async_session() as db:
        result = await db.execute(select(Session).where(Session.id == sid))
        session = result.scalar_one_or_none()
        if not session:
            return {"error": "Invalid session"}, 400

        canvas = Canvas(owner_session_id=sid)
        db.add(canvas)
        await db.commit()
        await db.refresh(canvas)
        return {
            "canvas_id": str(canvas.id),
            "hash_id": canvas.hash_id,
            "owner_session_id": str(canvas.owner_session_id),
        }


@app.get("/api/canvas/{canvas_hash}")
async def get_canvas(canvas_hash: str):
    """Look up a canvas by its short hash."""
    async with async_session() as db:
        result = await db.execute(
            select(Canvas).where(Canvas.hash_id == canvas_hash)
        )
        canvas = result.scalar_one_or_none()
        if not canvas:
            return {"error": "Canvas not found"}
        return {
            "canvas_id": str(canvas.id),
            "hash_id": canvas.hash_id,
            "owner_session_id": str(canvas.owner_session_id),
        }


@app.get("/api/canvas/{canvas_hash}/lines")
async def get_canvas_lines(canvas_hash: str):
    """Return all persisted lines for a specific canvas."""
    async with async_session() as db:
        # Look up canvas by hash
        canvas_result = await db.execute(
            select(Canvas).where(Canvas.hash_id == canvas_hash)
        )
        canvas = canvas_result.scalar_one_or_none()
        if not canvas:
            return []

        result = await db.execute(
            select(Line).where(Line.canvas_id == canvas.id)
        )
        lines = result.scalars().all()
        return [
            {
                "id": str(line.id),
                "session_id": str(line.session_id),
                "color": line.color,
                "points": line.points,
            }
            for line in lines
        ]


@app.get("/api/lines")
async def get_lines():
    """Return all persisted lines (legacy — returns all lines without canvas filter)."""
    async with async_session() as db:
        result = await db.execute(select(Line))
        lines = result.scalars().all()
        return [
            {
                "id": str(line.id),
                "session_id": str(line.session_id),
                "color": line.color,
                "points": line.points,
            }
            for line in lines
        ]


@app.get("/api/default-canvas")
async def get_or_create_default_canvas(session_id: str = Query(...)):
    """Get or create the default canvas for a session."""
    sid = uuid.UUID(session_id)
    async with async_session() as db:
        # Check if user already owns a canvas
        result = await db.execute(
            select(Canvas).where(Canvas.owner_session_id == sid).order_by(Canvas.created_at).limit(1)
        )
        canvas = result.scalar_one_or_none()
        if canvas:
            return {
                "canvas_id": str(canvas.id),
                "hash_id": canvas.hash_id,
                "owner_session_id": str(canvas.owner_session_id),
            }

        # Create default canvas for this user
        canvas = Canvas(owner_session_id=sid)
        db.add(canvas)
        await db.commit()
        await db.refresh(canvas)
        return {
            "canvas_id": str(canvas.id),
            "hash_id": canvas.hash_id,
            "owner_session_id": str(canvas.owner_session_id),
        }


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, session_id: str = Query(...), canvas_hash: str = Query(...)):
    sid = uuid.UUID(session_id)

    # Look up session and canvas
    async with async_session() as db:
        result = await db.execute(select(Session).where(Session.id == sid))
        session = result.scalar_one_or_none()
        if not session:
            await ws.close(code=4001, reason="Invalid session")
            return
        name = session.name
        color = session.color
        session.last_seen = datetime.now(timezone.utc)
        await db.commit()

        # Validate canvas exists
        canvas_result = await db.execute(
            select(Canvas).where(Canvas.hash_id == canvas_hash)
        )
        canvas = canvas_result.scalar_one_or_none()
        if not canvas:
            await ws.close(code=4002, reason="Invalid canvas")
            return
        canvas_id = canvas.id
        is_owner = canvas.owner_session_id == sid

    await manager.connect(sid, ws, name, color, canvas_hash)

    # Notify others on the same canvas that a user joined
    await manager.broadcast(
        {"type": "user_joined", "session_id": str(sid), "name": name, "color": color},
        exclude=sid,
        canvas_hash=canvas_hash,
    )
    # Send the joiner the current user list for this canvas
    await ws.send_text(json.dumps({
        "type": "users_list",
        "users": manager.active_users(canvas_hash=canvas_hash),
    }))
    # Send canvas info to the joiner
    await ws.send_text(json.dumps({
        "type": "canvas_info",
        "canvas_hash": canvas_hash,
        "is_owner": is_owner,
        "owner_session_id": str(canvas.owner_session_id),
    }))

    try:
        while True:
            data = json.loads(await ws.receive_text())
            msg_type = data.get("type")

            if msg_type == "cursor_move":
                manager.cursors[sid]["x"] = data["x"]
                manager.cursors[sid]["y"] = data["y"]
                manager.touch_activity(sid)
                await manager.broadcast(
                    {"type": "cursor_move", "session_id": str(sid),
                     "name": name, "color": color,
                     "x": data["x"], "y": data["y"]},
                    exclude=sid,
                    canvas_hash=canvas_hash,
                )

            elif msg_type == "draw_start":
                manager.touch_activity(sid)
                await manager.broadcast(
                    {"type": "draw_start", "session_id": str(sid),
                     "color": color, "x": data["x"], "y": data["y"]},
                    exclude=sid,
                    canvas_hash=canvas_hash,
                )

            elif msg_type == "draw_move":
                manager.touch_activity(sid)
                await manager.broadcast(
                    {"type": "draw_move", "session_id": str(sid),
                     "color": color, "x": data["x"], "y": data["y"]},
                    exclude=sid,
                    canvas_hash=canvas_hash,
                )

            elif msg_type == "draw_end":
                manager.touch_activity(sid)

                # Rate limiting check
                allowed, retry_after = manager.check_rate_limit(sid)
                if not allowed:
                    await ws.send_text(json.dumps({
                        "type": "rate_limited",
                        "retry_after": retry_after,
                    }))
                    continue

                # Persist the completed line
                points = data.get("points", [])
                if points:
                    manager.record_line(sid)
                    async with async_session() as db:
                        line = Line(
                            session_id=sid, canvas_id=canvas_id,
                            color=color, points=points
                        )
                        db.add(line)
                        await db.commit()
                        await db.refresh(line)
                        line_id = str(line.id)
                    # Confirm the line_id back to the sender
                    await ws.send_text(json.dumps({
                        "type": "draw_confirmed",
                        "line_id": line_id,
                    }))
                else:
                    line_id = None

                await manager.broadcast(
                    {"type": "draw_end", "session_id": str(sid),
                     "color": color, "line_id": line_id,
                     "points": points},
                    exclude=sid,
                    canvas_hash=canvas_hash,
                )

            elif msg_type == "undo_last_line":
                async with async_session() as db:
                    result = await db.execute(
                        select(Line)
                        .where(Line.session_id == sid, Line.canvas_id == canvas_id)
                        .order_by(Line.created_at.desc())
                        .limit(1)
                    )
                    last_line = result.scalar_one_or_none()
                    if last_line:
                        line_id = str(last_line.id)
                        await db.execute(delete(Line).where(Line.id == last_line.id))
                        await db.commit()
                        await manager.broadcast(
                            {"type": "line_deleted", "line_id": line_id,
                             "session_id": str(sid)},
                            canvas_hash=canvas_hash,
                        )

            elif msg_type == "delete_my_lines":
                async with async_session() as db:
                    await db.execute(
                        delete(Line).where(Line.session_id == sid, Line.canvas_id == canvas_id)
                    )
                    await db.commit()
                await manager.broadcast(
                    {"type": "lines_deleted", "session_id": str(sid)},
                    canvas_hash=canvas_hash,
                )

            elif msg_type == "clear_canvas":
                # Only the canvas owner can clear all lines
                if not is_owner:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Only the canvas owner can clear the canvas",
                    }))
                    continue
                async with async_session() as db:
                    await db.execute(
                        delete(Line).where(Line.canvas_id == canvas_id)
                    )
                    await db.commit()
                await manager.broadcast(
                    {"type": "canvas_cleared", "session_id": str(sid)},
                    canvas_hash=canvas_hash,
                )

    except WebSocketDisconnect:
        manager.disconnect(sid)
        await manager.broadcast(
            {"type": "user_left", "session_id": str(sid), "name": name},
            canvas_hash=canvas_hash,
        )


# ── Static files (must be last) ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
