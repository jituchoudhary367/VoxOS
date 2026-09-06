"""User ORM model."""

import uuid
from typing import Any

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from voxos.database.models.base import Base, TimestampMixin, UUIDMixin


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # JSON columns for flexible data
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # Relationships
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", lazy="select")
    memory_entries: Mapped[list["MemoryEntry"]] = relationship(back_populates="user", lazy="select")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"
