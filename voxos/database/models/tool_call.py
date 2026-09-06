"""ToolCall, Approval, Verification ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voxos.database.models.base import Base, TimestampMixin, UUIDMixin


class ToolCall(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"

    task_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    task_step: Mapped["TaskStep | None"] = relationship(back_populates="tool_calls", lazy="select")
    approval: Mapped["Approval | None"] = relationship(back_populates="tool_call", lazy="select")
    verification: Mapped["VerificationResult | None"] = relationship(
        back_populates="tool_call", lazy="select"
    )


class Approval(UUIDMixin, Base):
    __tablename__ = "approvals"

    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    requested_action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    consequences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    tool_call: Mapped["ToolCall"] = relationship(back_populates="approval", lazy="select")


class VerificationResult(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "verification_results"

    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # PASSED | FAILED | SKIPPED
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    tool_call: Mapped["ToolCall"] = relationship(back_populates="verification", lazy="select")
