"""Session and Conversation repositories."""

from voxos.database.models.session import Session, Conversation, Message
from voxos.database.repositories.base import BaseRepository

class SessionRepository(BaseRepository[Session]):
    """Repository for managing Session lifecycle."""
    def __init__(self, session):
        super().__init__(Session, session)

class ConversationRepository(BaseRepository[Conversation]):
    """Repository for managing Conversations."""
    def __init__(self, session):
        super().__init__(Conversation, session)

class MessageRepository(BaseRepository[Message]):
    """Repository for managing Messages."""
    def __init__(self, session):
        super().__init__(Message, session)
