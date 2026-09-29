"""Database adapter for durable conversation history."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from voxos.database.engine import get_session_factory
from voxos.database.repositories.conversation_repo import ConversationHistoryRepository
from voxos.database.repositories.session_repo import SessionRepository


class DatabaseConversationHistory:
    """Open a short-lived database session for each conversation-history operation."""

    def __init__(
        self,
        *,
        session_factory: Any = get_session_factory,
        repository_factory: Any = ConversationHistoryRepository,
        session_repository_factory: Any = SessionRepository,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._session_repository_factory = session_repository_factory

    async def append_turn(self, session_id: str, *, role: str, content: str) -> None:
        async with self._new_session() as session:
            session_uuid = uuid.UUID(session_id)
            session_repository = self._session_repository_factory(session)
            if await session_repository.get(session_uuid) is None:
                # The task manager inserts this row too, concurrently; losing
                # that race is fine, the row exists either way.
                try:
                    await session_repository.create(
                        {"id": session_uuid, "status": "active", "session_metadata": {}}
                    )
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
            repository = self._repository_factory(session)
            await repository.append_turn(session_uuid, role=role, content=content)
            await session.commit()

    async def list_recent(self, session_id: str, limit: int = 12) -> list[Any]:
        async with self._new_session() as session:
            repository = self._repository_factory(session)
            return list(await repository.list_recent(uuid.UUID(session_id), limit=limit))

    def _new_session(self) -> Any:
        """Support both a sessionmaker and the get_session_factory provider."""
        candidate = self._session_factory()
        if hasattr(candidate, "__aenter__"):
            return candidate
        return candidate()
