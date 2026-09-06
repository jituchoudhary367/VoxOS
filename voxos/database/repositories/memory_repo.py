"""
Memory Repository — async CRUD for MemoryEntry ORM model.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.database.models.memory import MemoryEntry


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict) -> MemoryEntry:
        entry = MemoryEntry(**data)
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, memory_id: str) -> MemoryEntry | None:
        result = await self._session.execute(
            select(MemoryEntry).where(MemoryEntry.id == memory_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[MemoryEntry]:
        result = await self._session.execute(
            select(MemoryEntry)
            .where(MemoryEntry.user_id == user_id)
            .order_by(MemoryEntry.importance.desc(), MemoryEntry.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def search(self, user_id: str, query: str, limit: int = 10) -> list[MemoryEntry]:
        """Simple keyword search on content field."""
        result = await self._session.execute(
            select(MemoryEntry)
            .where(
                MemoryEntry.user_id == user_id,
                MemoryEntry.content.ilike(f"%{query}%"),
            )
            .order_by(MemoryEntry.importance.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, memory_id: str) -> bool:
        entry = await self.get(memory_id)
        if entry:
            await self._session.delete(entry)
            return True
        return False

    async def delete_all_for_user(self, user_id: str) -> int:
        entries = await self.list_for_user(user_id, limit=1000)
        for e in entries:
            await self._session.delete(e)
        return len(entries)
