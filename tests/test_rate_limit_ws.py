"""WebSocket integration tests for rate limiting and activity tracking."""

import json
import time
import uuid
import asyncio

import pytest
from starlette.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

import app.main
from app.models import Session
from app.database import Base


async def _create_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _create_session(session_factory, name, color):
    async with session_factory() as db:
        s = Session(name=name, color=color)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


def _sync_create_session(session_factory, name, color):
    loop = asyncio.new_event_loop()
    sid = loop.run_until_complete(_create_session(session_factory, name, color))
    loop.close()
    return str(sid)


@pytest.fixture()
def ws_client(tmp_path):
    """Starlette TestClient with isolated DB for WebSocket testing."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_create_tables(engine))

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    original_session = app.main.async_session
    original_engine = app.main.engine
    app.main.async_session = factory
    app.main.engine = engine
    app.main.manager.active.clear()
    app.main.manager.cursors.clear()
    app.main.manager.last_activity.clear()
    app.main.manager.line_timestamps.clear()

    session_id = loop.run_until_complete(_create_session(factory, "Test Fox", "#e6194b"))

    client = TestClient(app.main.app)
    yield client, str(session_id), factory

    app.main.async_session = original_session
    app.main.engine = original_engine
    loop.run_until_complete(engine.dispose())
    loop.close()


class TestRateLimitWebSocket:
    def test_draw_end_under_limit_succeeds(self, ws_client):
        """Drawing under the rate limit should work normally."""
        client, session_id, _ = ws_client
        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list
            points = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))
            # Should not receive rate_limited — the draw_end broadcast goes
            # to others, and since we're alone we just verify no error.

    def test_rate_limited_response_when_exceeding_limit(self, ws_client):
        """Exceeding the rate limit should return a rate_limited message."""
        client, session_id, _ = ws_client

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list

            points = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]

            # Fill up the rate limit bucket
            for _ in range(100):
                ws.send_text(json.dumps({"type": "draw_end", "points": points}))

            # The 101st should be rate limited
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "rate_limited"
            assert "retry_after" in msg
            assert msg["retry_after"] > 0

    def test_rate_limit_does_not_persist_line(self, ws_client):
        """When rate limited, the line should not be saved to DB."""
        client, session_id, _ = ws_client

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list

            points = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]

            # Fill up the rate limit
            for _ in range(100):
                ws.send_text(json.dumps({"type": "draw_end", "points": points}))

            # This one should be rejected
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "rate_limited"

        # Verify only 100 lines were persisted
        resp = client.get("/api/lines")
        data = resp.json()
        assert len(data) == 100


class TestActivityTrackingWebSocket:
    def test_cursor_move_updates_activity(self, ws_client):
        """cursor_move should mark the user as active."""
        client, session_id, _ = ws_client
        sid_uuid = uuid.UUID(session_id)

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list

            # Set activity to old so we can detect the update
            app.main.manager.last_activity[sid_uuid] = 0

            ws.send_text(json.dumps({"type": "cursor_move", "x": 50, "y": 50}))
            # Give the server a moment to process
            assert app.main.manager.last_activity[sid_uuid] > 0

    def test_draw_start_updates_activity(self, ws_client):
        """draw_start should mark the user as active."""
        client, session_id, _ = ws_client
        sid_uuid = uuid.UUID(session_id)

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list
            app.main.manager.last_activity[sid_uuid] = 0

            ws.send_text(json.dumps({"type": "draw_start", "x": 0, "y": 0}))
            # Send a follow-up message and verify state after it processes
            ws.send_text(json.dumps({"type": "cursor_move", "x": 1, "y": 1}))
            assert app.main.manager.last_activity[sid_uuid] > 0

    def test_draw_end_updates_activity(self, ws_client):
        """draw_end should mark the user as active."""
        client, session_id, _ = ws_client
        sid_uuid = uuid.UUID(session_id)

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list
            app.main.manager.last_activity[sid_uuid] = 0

            ws.send_text(json.dumps({
                "type": "draw_end",
                "points": [{"x": 0, "y": 0}, {"x": 10, "y": 10}],
            }))
            # Send a follow-up message and verify state after it processes
            ws.send_text(json.dumps({"type": "cursor_move", "x": 1, "y": 1}))
            assert app.main.manager.last_activity[sid_uuid] > 0

    def test_users_list_includes_last_active(self, ws_client):
        """The users_list message should include last_active for each user."""
        client, session_id, _ = ws_client
        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "users_list"
            for user in msg["users"]:
                assert "last_active" in user

    def test_two_users_activity_isolated(self, ws_client):
        """Activity from one user should not affect another user's timestamp."""
        client, session_id, factory = ws_client
        session_id_2 = _sync_create_session(factory, "Bold Eagle", "#3cb44b")
        sid1 = uuid.UUID(session_id)
        sid2 = uuid.UUID(session_id_2)

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws1:
            ws1.receive_text()  # users_list

            with client.websocket_connect(f"/ws?session_id={session_id_2}") as ws2:
                ws2.receive_text()  # users_list
                ws1.receive_text()  # user_joined

                # Make user 1 stale
                app.main.manager.last_activity[sid1] = 0

                # User 2 moves cursor — should not update user 1
                ws2.send_text(json.dumps({"type": "cursor_move", "x": 10, "y": 10}))
                ws1.receive_text()  # cursor_move broadcast

                assert app.main.manager.last_activity[sid1] == 0
                assert app.main.manager.last_activity[sid2] > 0
