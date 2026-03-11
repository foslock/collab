"""Tests for database models."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Session, Line, Canvas


@pytest.mark.asyncio
class TestSessionModel:
    async def test_create_session(self, db_session):
        s = Session(name="Clever Bear", color="#3cb44b")
        db_session.add(s)
        await db_session.commit()
        await db_session.refresh(s)

        assert isinstance(s.id, uuid.UUID)
        assert s.name == "Clever Bear"
        assert s.color == "#3cb44b"
        assert s.created_at is not None
        assert s.last_seen is not None

    async def test_session_unique_name(self, db_session):
        s1 = Session(name="Unique Name", color="#e6194b")
        db_session.add(s1)
        await db_session.commit()

        s2 = Session(name="Unique Name", color="#3cb44b")
        db_session.add(s2)
        with pytest.raises(Exception):  # IntegrityError
            await db_session.commit()

    async def test_query_session_by_id(self, db_session, sample_session):
        result = await db_session.execute(
            select(Session).where(Session.id == sample_session.id)
        )
        found = result.scalar_one()
        assert found.name == sample_session.name


@pytest.mark.asyncio
class TestCanvasModel:
    async def test_create_canvas(self, db_session, sample_session):
        c = Canvas(owner_session_id=sample_session.id)
        db_session.add(c)
        await db_session.commit()
        await db_session.refresh(c)

        assert isinstance(c.id, uuid.UUID)
        assert len(c.hash_id) == 8
        assert c.owner_session_id == sample_session.id
        assert c.created_at is not None

    async def test_canvas_hash_is_unique(self, db_session, sample_session):
        c1 = Canvas(owner_session_id=sample_session.id)
        c2 = Canvas(owner_session_id=sample_session.id)
        db_session.add(c1)
        db_session.add(c2)
        await db_session.commit()
        await db_session.refresh(c1)
        await db_session.refresh(c2)
        assert c1.hash_id != c2.hash_id

    async def test_query_canvas_by_hash(self, db_session, sample_canvas):
        result = await db_session.execute(
            select(Canvas).where(Canvas.hash_id == sample_canvas.hash_id)
        )
        found = result.scalar_one()
        assert found.id == sample_canvas.id


@pytest.mark.asyncio
class TestLineModel:
    async def test_create_line(self, db_session, sample_session, sample_canvas):
        points = [{"x": 0, "y": 0}, {"x": 10, "y": 20}, {"x": 30, "y": 40}]
        line = Line(
            session_id=sample_session.id,
            canvas_id=sample_canvas.id,
            color=sample_session.color,
            points=points,
        )
        db_session.add(line)
        await db_session.commit()
        await db_session.refresh(line)

        assert isinstance(line.id, uuid.UUID)
        assert line.session_id == sample_session.id
        assert line.canvas_id == sample_canvas.id
        assert line.points == points

    async def test_query_lines_by_session(self, db_session, sample_lines, sample_session):
        result = await db_session.execute(
            select(Line).where(Line.session_id == sample_session.id)
        )
        lines = result.scalars().all()
        assert len(lines) == 3

    async def test_query_lines_by_canvas(self, db_session, sample_lines, sample_canvas):
        result = await db_session.execute(
            select(Line).where(Line.canvas_id == sample_canvas.id)
        )
        lines = result.scalars().all()
        assert len(lines) == 3

    async def test_delete_lines_by_session(self, db_session, sample_lines, sample_session):
        from sqlalchemy import delete
        await db_session.execute(
            delete(Line).where(Line.session_id == sample_session.id)
        )
        await db_session.commit()

        result = await db_session.execute(
            select(Line).where(Line.session_id == sample_session.id)
        )
        assert result.scalars().all() == []
