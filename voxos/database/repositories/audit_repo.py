"""
Audit Log Repository — async queries for the AuditLog ORM model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.database.models.memory import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: dict[str, Any]) -> AuditLog:
        entry = AuditLog(**data)
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list(
        self,
        session_id: str | None = None,
        task_id: str | None = None,
        user_id: str | None = None,
        event_type: str | None = None,
        risk_level: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

        filters = []
        if session_id:
            filters.append(AuditLog.session_id == session_id)
        if task_id:
            filters.append(AuditLog.task_id == task_id)
        if user_id:
            filters.append(AuditLog.user_id == user_id)
        if event_type:
            filters.append(AuditLog.event_type == event_type)
        if risk_level:
            filters.append(AuditLog.risk_level == risk_level)
        if since:
            filters.append(AuditLog.created_at >= since)

        if filters:
            stmt = stmt.where(and_(*filters))

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_event_type(self, since: datetime | None = None) -> dict[str, int]:
        """Count audit entries grouped by event_type."""
        entries = await self.list(since=since, limit=10000)
        counts: dict[str, int] = {}
        for e in entries:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        return counts
