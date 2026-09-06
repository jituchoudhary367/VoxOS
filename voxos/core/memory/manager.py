"""
Long-term Memory Manager.

Handles extracting, storing, and retrieving facts, user preferences,
and important context using standard DB.
"""

from __future__ import annotations

import uuid
from typing import Any
from pydantic import BaseModel
from sqlalchemy import select, or_

from voxos.observability.logger import get_logger
from voxos.database.engine import get_async_session, get_session_factory
from voxos.database.models.memory import MemoryEntry as DBMemoryEntry

log = get_logger(__name__)


class MemoryEntry(BaseModel):
    id: str
    user_id: str
    content: str
    category: str = "general"
    importance: int = 1


class MemoryManager:
    """Manages agent memory using SQLAlchemy."""

    async def add_memory(self, user_id: str, content: str, category: str = "general", importance: int = 1) -> MemoryEntry:
        """Store a new fact or preference."""
        try:
            parsed_user_id = uuid.UUID(user_id)
        except ValueError:
            # For testing/dev without a real UUID user
            parsed_user_id = uuid.uuid4()
            
        async_session_factory = get_session_factory()
        
        async with async_session_factory() as session:
            entry = DBMemoryEntry(
                user_id=parsed_user_id,
                memory_type=category,
                content=content,
                importance=float(importance),
                source="user",
                privacy_level="private"
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            
            log.info("memory_added", user_id=str(entry.user_id), category=category, length=len(content))
            
            return MemoryEntry(
                id=str(entry.id),
                user_id=str(entry.user_id),
                content=entry.content,
                category=entry.memory_type,
                importance=int(entry.importance)
            )

    async def get_relevant_memories(self, user_id: str, query: str | None = None, limit: int = 5) -> list[MemoryEntry]:
        """Retrieve relevant memories for a user."""
        try:
            parsed_user_id = uuid.UUID(user_id)
        except ValueError:
            return []
            
        async_session_factory = get_session_factory()
        async with async_session_factory() as session:
            stmt = select(DBMemoryEntry).where(DBMemoryEntry.user_id == parsed_user_id)
            
            if query:
                stmt = stmt.where(DBMemoryEntry.content.ilike(f"%{query}%"))
                
            stmt = stmt.order_by(DBMemoryEntry.importance.desc()).limit(limit)
            result = await session.execute(stmt)
            db_entries = result.scalars().all()
            
            return [
                MemoryEntry(
                    id=str(e.id),
                    user_id=str(e.user_id),
                    content=e.content,
                    category=e.memory_type,
                    importance=int(e.importance)
                ) for e in db_entries
            ]


# Singleton
_memory_manager: MemoryManager | None = None

def get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
