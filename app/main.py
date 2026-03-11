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
from app.models import Session, Line
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

    async def connect(self, session_id: uuid.UUID, ws: WebSocket, name: str, color: str):
        await ws.accept()
        self.active[session_id] = ws
        self.cursors[session_id] = {"name": name, "color": color, "x": 0, "y": 0}
        self.last_activity[session_id] = time.monotonic()

    def disconnect(self, session_id: uuid.UUID):
        self.active.pop(session_id, None)
        self.cursors.pop(session_id, None)
        self.last_activity.pop(session_id, None)
        self.line_timestamps.pop(session_id, None)

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

    async def broadcast(self, message: dict, exclude: uuid.UUID | None = None):
        data = json.dumps(message)
        dead = []
        for sid, ws in self.active.items():
            if sid == exclude:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(sid)
        for sid in dead:
            self.disconnect(sid)

    def active_users(self) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "session_id": str(sid),
                "last_active": now - self.last_activity.get(sid, now),
                **info,
            }
            for sid, info in self.cursors.items()
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


@app.get("/api/lines")
async def get_lines():
    """Return all persisted lines."""
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


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, session_id: str = Query(...)):
    sid = uuid.UUID(session_id)

    # Look up session
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

    await manager.connect(sid, ws, name, color)

    # Notify others that a user joined
    await manager.broadcast(
        {"type": "user_joined", "session_id": str(sid), "name": name, "color": color},
        exclude=sid,
    )
    # Send the joiner the current user list
    await ws.send_text(json.dumps({
        "type": "users_list",
        "users": manager.active_users(),
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
                )

            elif msg_type == "draw_start":
                manager.touch_activity(sid)
                await manager.broadcast(
                    {"type": "draw_start", "session_id": str(sid),
                     "color": color, "x": data["x"], "y": data["y"]},
                    exclude=sid,
                )

            elif msg_type == "draw_move":
                manager.touch_activity(sid)
                await manager.broadcast(
                    {"type": "draw_move", "session_id": str(sid),
                     "color": color, "x": data["x"], "y": data["y"]},
                    exclude=sid,
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
                            session_id=sid, color=color, points=points
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
                )

            elif msg_type == "undo_last_line":
                async with async_session() as db:
                    result = await db.execute(
                        select(Line)
                        .where(Line.session_id == sid)
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
                        )

            elif msg_type == "delete_my_lines":
                async with async_session() as db:
                    await db.execute(
                        delete(Line).where(Line.session_id == sid)
                    )
                    await db.commit()
                await manager.broadcast(
                    {"type": "lines_deleted", "session_id": str(sid)},
                )

    except WebSocketDisconnect:
        manager.disconnect(sid)
        await manager.broadcast(
            {"type": "user_left", "session_id": str(sid), "name": name},
        )


# ── Static files (must be last) ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
