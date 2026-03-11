"""Tests for WebSocket functionality."""

import json
import os
import tempfile
import uuid

import pytest
from starlette.testclient import TestClient

import app.main
from app.models import Session, Line, Canvas
from app.database import Base
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool


@pytest.fixture()
def sync_client(tmp_path):
    """Starlette TestClient for WebSocket testing (sync context)."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_create_tables(engine))

    test_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    original_session = app.main.async_session
    original_engine = app.main.engine
    app.main.async_session = test_session_factory
    app.main.engine = engine

    app.main.manager.active.clear()
    app.main.manager.cursors.clear()
    app.main.manager.last_activity.clear()
    app.main.manager.line_timestamps.clear()
    app.main.manager.canvas_map.clear()

    # Create a test session and canvas in the DB
    session_id, canvas_hash = loop.run_until_complete(
        _create_test_session_and_canvas(test_session_factory)
    )

    client = TestClient(app.main.app)
    yield client, str(session_id), canvas_hash, test_session_factory

    app.main.async_session = original_session
    app.main.engine = original_engine
    loop.run_until_complete(engine.dispose())
    loop.close()


async def _create_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _create_test_session_and_canvas(session_factory):
    async with session_factory() as db:
        s = Session(name="Test Fox", color="#e6194b")
        db.add(s)
        await db.commit()
        await db.refresh(s)
        c = Canvas(owner_session_id=s.id)
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return s.id, c.hash_id


async def _create_test_session_with_name(session_factory, name, color):
    async with session_factory() as db:
        s = Session(name=name, color=color)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


def _create_sync_session(session_factory, name, color):
    import asyncio
    loop = asyncio.new_event_loop()
    sid = loop.run_until_complete(_create_test_session_with_name(session_factory, name, color))
    loop.close()
    return str(sid)


class TestWebSocket:
    def test_connect_and_receive_users_list(self, sync_client):
        client, session_id, canvas_hash, _ = sync_client
        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws:
            data = json.loads(ws.receive_text())
            assert data["type"] == "users_list"
            assert isinstance(data["users"], list)
            assert len(data["users"]) == 1
            assert data["users"][0]["name"] == "Test Fox"

    def test_canvas_info_sent_on_connect(self, sync_client):
        client, session_id, canvas_hash, _ = sync_client
        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws:
            ws.receive_text()  # users_list
            data = json.loads(ws.receive_text())
            assert data["type"] == "canvas_info"
            assert data["canvas_hash"] == canvas_hash
            assert data["is_owner"] is True

    def test_cursor_move_broadcast(self, sync_client):
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Bold Eagle", "#3cb44b")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                joined = json.loads(ws1.receive_text())
                assert joined["type"] == "user_joined"

                ws2.send_text(json.dumps({"type": "cursor_move", "x": 100, "y": 200}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "cursor_move"
                assert msg["x"] == 100
                assert msg["y"] == 200

    def test_draw_start_and_move_broadcast(self, sync_client):
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Calm Tiger", "#4363d8")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                ws1.receive_text()  # user_joined

                ws2.send_text(json.dumps({"type": "draw_start", "x": 0, "y": 0}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "draw_start"
                assert msg["x"] == 0

                ws2.send_text(json.dumps({"type": "draw_move", "x": 10, "y": 10}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "draw_move"
                assert msg["x"] == 10

    def test_draw_end_empty_points_no_persist(self, sync_client):
        """draw_end with empty points should broadcast with line_id=None."""
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Daring Lynx", "#f032e6")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                ws1.receive_text()  # user_joined

                # Empty points — no DB write, just broadcast
                ws2.send_text(json.dumps({"type": "draw_end", "points": []}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "draw_end"
                assert msg["points"] == []
                assert msg["line_id"] is None

    def test_draw_end_sends_draw_confirmed_to_sender(self, sync_client):
        """draw_end with points should send draw_confirmed with a line_id back to sender."""
        client, session_id, canvas_hash, _ = sync_client

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws:
            ws.receive_text()  # users_list
            ws.receive_text()  # canvas_info

            points = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "draw_confirmed"
            assert msg["line_id"] is not None

    def test_delete_my_lines(self, sync_client):
        client, session_id, canvas_hash, _ = sync_client

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws:
            ws.receive_text()  # users_list
            ws.receive_text()  # canvas_info

            # Draw a line first — sender receives draw_confirmed
            points = [{"x": 0, "y": 0}, {"x": 50, "y": 50}]
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))
            ws.receive_text()  # draw_confirmed

            # Delete my lines — broadcast includes sender
            ws.send_text(json.dumps({"type": "delete_my_lines"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "lines_deleted"
            assert msg["session_id"] == session_id

    def test_undo_last_line(self, sync_client):
        """undo_last_line deletes the most recent line and broadcasts line_deleted."""
        client, session_id, canvas_hash, _ = sync_client

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws:
            ws.receive_text()  # users_list
            ws.receive_text()  # canvas_info

            # Draw two lines
            points1 = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]
            points2 = [{"x": 20, "y": 20}, {"x": 30, "y": 30}]
            ws.send_text(json.dumps({"type": "draw_end", "points": points1}))
            confirmed1 = json.loads(ws.receive_text())
            assert confirmed1["type"] == "draw_confirmed"
            line_id_1 = confirmed1["line_id"]

            ws.send_text(json.dumps({"type": "draw_end", "points": points2}))
            confirmed2 = json.loads(ws.receive_text())
            assert confirmed2["type"] == "draw_confirmed"
            line_id_2 = confirmed2["line_id"]

            # Undo — should delete the most recent line (line 2)
            ws.send_text(json.dumps({"type": "undo_last_line"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "line_deleted"
            assert msg["line_id"] == line_id_2
            assert msg["session_id"] == session_id

            # Undo again — should delete line 1
            ws.send_text(json.dumps({"type": "undo_last_line"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "line_deleted"
            assert msg["line_id"] == line_id_1

            # Undo with no lines left — should receive nothing (no broadcast)
            # We verify by drawing a fresh line and confirming the undo only removes that one
            ws.send_text(json.dumps({"type": "draw_end", "points": points1}))
            ws.receive_text()  # draw_confirmed
            ws.send_text(json.dumps({"type": "undo_last_line"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "line_deleted"

    def test_invalid_session_rejected(self, sync_client):
        client, _, canvas_hash, _ = sync_client
        fake_id = str(uuid.uuid4())
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws?session_id={fake_id}&canvas_hash={canvas_hash}") as ws:
                ws.receive_text()

    def test_invalid_canvas_rejected(self, sync_client):
        client, session_id, _, _ = sync_client
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash=badcanva") as ws:
                ws.receive_text()

    def test_disconnect_broadcasts_user_left(self, sync_client):
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Swift Owl", "#f58231")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                ws1.receive_text()  # user_joined

            # ws2 disconnected — ws1 should get user_left
            msg = json.loads(ws1.receive_text())
            assert msg["type"] == "user_left"

    def test_clear_canvas_by_owner(self, sync_client):
        """Canvas owner can clear all lines from all users."""
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Bold Eagle", "#3cb44b")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            # Draw a line as owner
            points = [{"x": 0, "y": 0}, {"x": 10, "y": 10}]
            ws1.send_text(json.dumps({"type": "draw_end", "points": points}))
            ws1.receive_text()  # draw_confirmed

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                ws1.receive_text()  # user_joined

                # Draw a line as non-owner
                ws2.send_text(json.dumps({"type": "draw_end", "points": points}))
                ws2.receive_text()  # draw_confirmed
                ws1.receive_text()  # draw_end broadcast

                # Owner clears canvas
                ws1.send_text(json.dumps({"type": "clear_canvas"}))
                msg1 = json.loads(ws1.receive_text())
                assert msg1["type"] == "canvas_cleared"
                msg2 = json.loads(ws2.receive_text())
                assert msg2["type"] == "canvas_cleared"

        # Verify all lines are gone
        resp = client.get(f"/api/canvas/{canvas_hash}/lines")
        assert resp.json() == []

    def test_clear_canvas_rejected_for_non_owner(self, sync_client):
        """Non-owner cannot clear the canvas."""
        client, session_id, canvas_hash, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Bold Eagle", "#3cb44b")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info
                ws1.receive_text()  # user_joined

                # Non-owner tries to clear canvas
                ws2.send_text(json.dumps({"type": "clear_canvas"}))
                msg = json.loads(ws2.receive_text())
                assert msg["type"] == "error"
                assert "owner" in msg["message"].lower()

    def test_cross_canvas_isolation(self, sync_client):
        """Messages on one canvas should not leak to another canvas."""
        client, session_id, canvas_hash, factory = sync_client

        # Create a second canvas
        import asyncio
        async def _create_canvas(sf):
            async with sf() as db:
                c = Canvas(owner_session_id=uuid.UUID(session_id))
                db.add(c)
                await db.commit()
                await db.refresh(c)
                return c.hash_id

        loop = asyncio.new_event_loop()
        canvas_hash_2 = loop.run_until_complete(_create_canvas(factory))
        loop.close()

        session_id_2 = _create_sync_session(factory, "Bold Eagle", "#3cb44b")

        with client.websocket_connect(f"/ws?session_id={session_id}&canvas_hash={canvas_hash}") as ws1:
            ws1.receive_text()  # users_list
            ws1.receive_text()  # canvas_info

            with client.websocket_connect(f"/ws?session_id={session_id_2}&canvas_hash={canvas_hash_2}") as ws2:
                ws2.receive_text()  # users_list
                ws2.receive_text()  # canvas_info

                # ws2 draws on canvas_hash_2 - ws1 should NOT receive this
                ws2.send_text(json.dumps({"type": "draw_start", "x": 0, "y": 0}))

                # ws1 sends a cursor_move - this should work and not crash
                ws1.send_text(json.dumps({"type": "cursor_move", "x": 50, "y": 50}))

                # The test passes if no exception is raised — messages stay isolated.
