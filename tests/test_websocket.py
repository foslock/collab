"""Tests for WebSocket functionality."""

import json
import os
import tempfile
import uuid

import pytest
from starlette.testclient import TestClient

import app.main
from app.models import Session, Line
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

    # Create a test session in the DB
    session_id = loop.run_until_complete(_create_test_session(test_session_factory))

    client = TestClient(app.main.app)
    yield client, str(session_id), test_session_factory

    app.main.async_session = original_session
    app.main.engine = original_engine
    loop.run_until_complete(engine.dispose())
    loop.close()


async def _create_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _create_test_session(session_factory):
    async with session_factory() as db:
        s = Session(name="Test Fox", color="#e6194b")
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


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
        client, session_id, _ = sync_client
        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            data = json.loads(ws.receive_text())
            assert data["type"] == "users_list"
            assert isinstance(data["users"], list)
            assert len(data["users"]) == 1
            assert data["users"][0]["name"] == "Test Fox"

    def test_cursor_move_broadcast(self, sync_client):
        client, session_id, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Bold Eagle", "#3cb44b")

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws1:
            ws1.receive_text()  # users_list

            with client.websocket_connect(f"/ws?session_id={session_id_2}") as ws2:
                ws2.receive_text()  # users_list
                joined = json.loads(ws1.receive_text())
                assert joined["type"] == "user_joined"

                ws2.send_text(json.dumps({"type": "cursor_move", "x": 100, "y": 200}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "cursor_move"
                assert msg["x"] == 100
                assert msg["y"] == 200

    def test_draw_start_and_move_broadcast(self, sync_client):
        client, session_id, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Calm Tiger", "#4363d8")

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws1:
            ws1.receive_text()  # users_list

            with client.websocket_connect(f"/ws?session_id={session_id_2}") as ws2:
                ws2.receive_text()  # users_list
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
        client, session_id, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Daring Lynx", "#f032e6")

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws1:
            ws1.receive_text()  # users_list

            with client.websocket_connect(f"/ws?session_id={session_id_2}") as ws2:
                ws2.receive_text()  # users_list
                ws1.receive_text()  # user_joined

                # Empty points — no DB write, just broadcast
                ws2.send_text(json.dumps({"type": "draw_end", "points": []}))
                msg = json.loads(ws1.receive_text())
                assert msg["type"] == "draw_end"
                assert msg["points"] == []
                assert msg["line_id"] is None

    def test_delete_my_lines(self, sync_client):
        client, session_id, _ = sync_client

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws:
            ws.receive_text()  # users_list

            # Draw a line first
            points = [{"x": 0, "y": 0}, {"x": 50, "y": 50}]
            ws.send_text(json.dumps({"type": "draw_end", "points": points}))

            # Delete my lines — broadcast includes sender
            ws.send_text(json.dumps({"type": "delete_my_lines"}))
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "lines_deleted"
            assert msg["session_id"] == session_id

    def test_invalid_session_rejected(self, sync_client):
        client, _, _ = sync_client
        fake_id = str(uuid.uuid4())
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws?session_id={fake_id}") as ws:
                ws.receive_text()

    def test_disconnect_broadcasts_user_left(self, sync_client):
        client, session_id, factory = sync_client

        session_id_2 = _create_sync_session(factory, "Swift Owl", "#f58231")

        with client.websocket_connect(f"/ws?session_id={session_id}") as ws1:
            ws1.receive_text()  # users_list

            with client.websocket_connect(f"/ws?session_id={session_id_2}") as ws2:
                ws2.receive_text()  # users_list
                ws1.receive_text()  # user_joined

            # ws2 disconnected — ws1 should get user_left
            msg = json.loads(ws1.receive_text())
            assert msg["type"] == "user_left"
