import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base
from app.models import Session, Line

# In-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture()
async def client(db_engine, monkeypatch):
    """Create a test client with the test database patched in."""
    test_session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    # Patch the engine and async_session used throughout the app
    import app.main
    monkeypatch.setattr(app.main, "engine", db_engine)
    monkeypatch.setattr(app.main, "async_session", test_session_factory)

    # Reset the connection manager state between tests
    app.main.manager.active.clear()
    app.main.manager.cursors.clear()
    app.main.manager.last_activity.clear()
    app.main.manager.line_timestamps.clear()

    transport = ASGITransport(app=app.main.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture()
async def sample_session(db_session):
    """Insert a sample session into the test DB."""
    s = Session(name="Bold Fox", color="#e6194b")
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    return s


@pytest_asyncio.fixture()
async def sample_lines(db_session, sample_session):
    """Insert sample lines for the sample session."""
    lines = []
    for i in range(3):
        line = Line(
            session_id=sample_session.id,
            color=sample_session.color,
            points=[{"x": i * 10, "y": i * 10}, {"x": i * 10 + 5, "y": i * 10 + 5}],
        )
        db_session.add(line)
        lines.append(line)
    await db_session.commit()
    for line in lines:
        await db_session.refresh(line)
    return lines
