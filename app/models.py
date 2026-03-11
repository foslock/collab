import hashlib
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _generate_canvas_hash() -> str:
    """Generate an 8-character unique hash for a canvas."""
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:8]


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)  # hex color
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Canvas(Base):
    __tablename__ = "canvases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    hash_id: Mapped[str] = mapped_column(
        String(8), unique=True, nullable=False, default=_generate_canvas_hash
    )
    owner_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Line(Base):
    __tablename__ = "lines"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), nullable=False, index=True
    )
    canvas_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), nullable=False, index=True
    )
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    # stored as list of {x, y} points
    points: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
