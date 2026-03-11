"""Tests for the ConnectionManager class."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.main import ConnectionManager


@pytest.mark.asyncio
class TestConnectionManager:
    async def test_connect_and_active_users(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()

        await mgr.connect(sid, ws, "Bold Fox", "#e6194b", "abcd1234")

        assert sid in mgr.active
        assert sid in mgr.cursors
        assert mgr.cursors[sid]["name"] == "Bold Fox"
        assert mgr.cursors[sid]["color"] == "#e6194b"
        assert mgr.canvas_map[sid] == "abcd1234"
        ws.accept.assert_called_once()

    async def test_disconnect(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()

        await mgr.connect(sid, ws, "Bold Fox", "#e6194b", "abcd1234")
        mgr.disconnect(sid)

        assert sid not in mgr.active
        assert sid not in mgr.cursors
        assert sid not in mgr.canvas_map

    async def test_disconnect_nonexistent(self):
        mgr = ConnectionManager()
        mgr.disconnect(uuid.uuid4())  # should not raise

    async def test_broadcast_sends_to_all(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        sid1, sid2 = uuid.uuid4(), uuid.uuid4()

        await mgr.connect(sid1, ws1, "A", "#aaa", "canvas1")
        await mgr.connect(sid2, ws2, "B", "#bbb", "canvas1")

        await mgr.broadcast({"type": "test"})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        msg = json.loads(ws1.send_text.call_args[0][0])
        assert msg["type"] == "test"

    async def test_broadcast_excludes_sender(self):
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        sid1, sid2 = uuid.uuid4(), uuid.uuid4()

        await mgr.connect(sid1, ws1, "A", "#aaa", "canvas1")
        await mgr.connect(sid2, ws2, "B", "#bbb", "canvas1")

        await mgr.broadcast({"type": "test"}, exclude=sid1)

        ws1.send_text.assert_not_called()
        ws2.send_text.assert_called_once()

    async def test_broadcast_removes_dead_connections(self):
        mgr = ConnectionManager()
        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = RuntimeError("connection lost")

        sid_ok, sid_dead = uuid.uuid4(), uuid.uuid4()
        await mgr.connect(sid_ok, ws_ok, "A", "#aaa", "canvas1")
        await mgr.connect(sid_dead, ws_dead, "B", "#bbb", "canvas1")

        await mgr.broadcast({"type": "test"})

        assert sid_ok in mgr.active
        assert sid_dead not in mgr.active

    async def test_active_users_returns_list(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()
        await mgr.connect(sid, ws, "Fox", "#abc", "canvas1")

        users = mgr.active_users()
        assert len(users) == 1
        assert users[0]["session_id"] == str(sid)
        assert users[0]["name"] == "Fox"
        assert users[0]["color"] == "#abc"

    async def test_broadcast_canvas_scoped(self):
        """broadcast with canvas_hash only sends to peers on the same canvas."""
        mgr = ConnectionManager()
        ws1, ws2, ws3 = AsyncMock(), AsyncMock(), AsyncMock()
        sid1, sid2, sid3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        await mgr.connect(sid1, ws1, "A", "#aaa", "canvas1")
        await mgr.connect(sid2, ws2, "B", "#bbb", "canvas1")
        await mgr.connect(sid3, ws3, "C", "#ccc", "canvas2")

        await mgr.broadcast({"type": "test"}, canvas_hash="canvas1")

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        ws3.send_text.assert_not_called()

    async def test_active_users_canvas_scoped(self):
        """active_users with canvas_hash only returns users on the same canvas."""
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        sid1, sid2 = uuid.uuid4(), uuid.uuid4()

        await mgr.connect(sid1, ws1, "A", "#aaa", "canvas1")
        await mgr.connect(sid2, ws2, "B", "#bbb", "canvas2")

        users = mgr.active_users(canvas_hash="canvas1")
        assert len(users) == 1
        assert users[0]["name"] == "A"
