"""Task, TaskStep ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voxos.database.models.base import Base, TimestampMixin, UUIDMixin


class Task(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tasks"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_request: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="CREATED", nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    session: Mapped["Session"] = relationship(back_populates="tasks", lazy="select")
    steps: Mapped[list["TaskStep"]] = relationship(
        back_populates="task", lazy="select", order_by="TaskStep.step_order"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="task", lazy="select")


class TaskStep(UUIDMixin, Base):
    __tablename__ = "task_steps"

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    task: Mapped["Task"] = relationship(back_populates="steps", lazy="select")
    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="task_step", lazy="select")
