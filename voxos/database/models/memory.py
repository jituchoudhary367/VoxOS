"""Memory and AuditLog ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voxos.database.models.base import Base, TimestampMixin, UUIDMixin


class MemoryEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memory_entries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # working | conversation | preference | episodic | semantic
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    privacy_level: Mapped[str] = mapped_column(String(20), default="private", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="memory_entries", lazy="select")


class AuditLog(UUIDMixin, Base):
    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    log_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    session: Mapped["Session | None"] = relationship(back_populates="audit_logs", lazy="select")
    task: Mapped["Task | None"] = relationship(back_populates="audit_logs", lazy="select")
