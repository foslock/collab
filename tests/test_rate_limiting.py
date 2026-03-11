"""Tests for rate limiting and activity tracking."""

import json
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.main import ConnectionManager, RATE_LIMIT_LINES, RATE_LIMIT_WINDOW, ACTIVITY_TIMEOUT


class TestRateLimiting:
    """Tests for the ConnectionManager rate limiting logic."""

    def test_check_rate_limit_allows_under_limit(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        allowed, retry = mgr.check_rate_limit(sid)
        assert allowed is True
        assert retry == 0

    def test_record_line_and_check_under_limit(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        for _ in range(RATE_LIMIT_LINES - 1):
            mgr.record_line(sid)
        allowed, retry = mgr.check_rate_limit(sid)
        assert allowed is True

    def test_check_rate_limit_blocks_at_limit(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        for _ in range(RATE_LIMIT_LINES):
            mgr.record_line(sid)
        allowed, retry = mgr.check_rate_limit(sid)
        assert allowed is False
        assert retry > 0

    def test_rate_limit_per_user_isolation(self):
        """Rate limit for one user should not affect another."""
        mgr = ConnectionManager()
        sid1 = uuid.uuid4()
        sid2 = uuid.uuid4()
        for _ in range(RATE_LIMIT_LINES):
            mgr.record_line(sid1)
        allowed1, _ = mgr.check_rate_limit(sid1)
        allowed2, _ = mgr.check_rate_limit(sid2)
        assert allowed1 is False
        assert allowed2 is True

    def test_rate_limit_expires_after_window(self):
        """Old timestamps should be pruned so the limit resets."""
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        # Inject timestamps that are older than the window
        old_time = time.monotonic() - RATE_LIMIT_WINDOW - 1
        mgr.line_timestamps[sid] = [old_time] * RATE_LIMIT_LINES
        allowed, _ = mgr.check_rate_limit(sid)
        assert allowed is True
        # Old entries should have been pruned
        assert len(mgr.line_timestamps[sid]) == 0

    def test_disconnect_clears_rate_limit_data(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        mgr.record_line(sid)
        assert sid in mgr.line_timestamps
        mgr.disconnect(sid)
        assert sid not in mgr.line_timestamps


class TestActivityTracking:
    """Tests for the ConnectionManager activity tracking."""

    @pytest.mark.asyncio
    async def test_connect_sets_initial_activity(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()
        await mgr.connect(sid, ws, "Fox", "#abc", "canvas1")
        assert sid in mgr.last_activity

    def test_touch_activity_updates_timestamp(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        mgr.last_activity[sid] = 0  # very old
        mgr.touch_activity(sid)
        assert mgr.last_activity[sid] > 0

    @pytest.mark.asyncio
    async def test_active_users_includes_last_active(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()
        await mgr.connect(sid, ws, "Fox", "#abc", "canvas1")
        users = mgr.active_users()
        assert len(users) == 1
        assert "last_active" in users[0]
        # Just connected, should be very recent
        assert users[0]["last_active"] < 1.0

    @pytest.mark.asyncio
    async def test_active_users_shows_stale_for_old_activity(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        ws = AsyncMock()
        await mgr.connect(sid, ws, "Fox", "#abc", "canvas1")
        # Simulate old activity
        mgr.last_activity[sid] = time.monotonic() - 30
        users = mgr.active_users()
        assert users[0]["last_active"] >= 29  # at least 29s ago

    def test_disconnect_clears_activity(self):
        mgr = ConnectionManager()
        sid = uuid.uuid4()
        mgr.last_activity[sid] = time.monotonic()
        mgr.disconnect(sid)
        assert sid not in mgr.last_activity


class TestRateLimitConstants:
    """Verify the rate limit constants are reasonable."""

    def test_rate_limit_lines_is_100(self):
        assert RATE_LIMIT_LINES == 100

    def test_rate_limit_window_is_60(self):
        assert RATE_LIMIT_WINDOW == 60

    def test_activity_timeout_is_10(self):
        assert ACTIVITY_TIMEOUT == 10
