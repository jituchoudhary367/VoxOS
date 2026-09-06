"""Session and Conversation ORM models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voxos.database.models.base import Base, TimestampMixin, UUIDMixin


class Session(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    session_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="sessions", lazy="select")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="session", lazy="select")
    tasks: Mapped[list["Task"]] = relationship(back_populates="session", lazy="select")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="session", lazy="select")


class Conversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)

    # Relationships
    session: Mapped["Session"] = relationship(back_populates="conversations", lazy="select")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", lazy="select")


class Message(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    input_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text | voice
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages", lazy="select")
