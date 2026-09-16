"""Session-scoped durable conversation history."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.database.models.session import Conversation, Message


class ConversationHistoryRepository:
    """Read and write the message history associated with a VoxOS session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append_turn(
        self,
        session_id: uuid.UUID,
        *,
        role: str,
        content: str,
    ) -> Message:
        """Append a user or assistant message to the session's conversation."""
        conversation = await self._get_or_create_conversation(session_id)
        message = Message(
            conversation_id=conversation.id,
            role=role,
            content=content,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_recent(self, session_id: uuid.UUID, limit: int = 12) -> Sequence[Message]:
        """Return the newest messages for one session in conversational order."""
        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(reversed(result.scalars().all()))

    async def _get_or_create_conversation(self, session_id: uuid.UUID) -> Conversation:
        statement = select(Conversation).where(Conversation.session_id == session_id)
        result = await self.session.execute(statement)
        conversation = result.scalar_one_or_none()
        if conversation is not None:
            return conversation

        conversation = Conversation(session_id=session_id)
        self.session.add(conversation)
        await self.session.flush()
        return conversation
