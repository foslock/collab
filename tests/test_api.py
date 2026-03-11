"""Tests for REST API endpoints."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Session, Line


@pytest.mark.asyncio
class TestGetOrCreateSession:
    async def test_create_new_session(self, client):
        resp = await client.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "name" in data
        assert "color" in data
        # name should be "Adjective Animal"
        assert " " in data["name"]

    async def test_resume_existing_session(self, client, sample_session):
        resp = await client.get(f"/api/session?session_id={sample_session.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == str(sample_session.id)
        assert data["name"] == sample_session.name
        assert data["color"] == sample_session.color

    async def test_invalid_session_id_creates_new(self, client):
        resp = await client.get("/api/session?session_id=not-a-uuid")
        assert resp.status_code == 200
        data = resp.json()
        # Should still get a valid session
        assert "session_id" in data
        assert "name" in data

    async def test_nonexistent_session_id_creates_new(self, client):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/session?session_id={fake_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] != fake_id

    async def test_multiple_sessions_get_unique_names(self, client):
        names = set()
        for _ in range(10):
            resp = await client.get("/api/session")
            data = resp.json()
            names.add(data["name"])
        assert len(names) == 10


@pytest.mark.asyncio
class TestGetLines:
    async def test_empty_lines(self, client):
        resp = await client.get("/api/lines")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_existing_lines(self, client, sample_lines):
        resp = await client.get("/api/lines")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for line in data:
            assert "id" in line
            assert "session_id" in line
            assert "color" in line
            assert "points" in line
            assert len(line["points"]) == 2
