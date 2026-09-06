"""
Import all models here so Alembic can detect them for autogenerate.
"""

from voxos.database.models.base import Base  # noqa: F401
from voxos.database.models.memory import AuditLog, MemoryEntry  # noqa: F401
from voxos.database.models.session import Conversation, Message, Session  # noqa: F401
from voxos.database.models.task import Task, TaskStep  # noqa: F401
from voxos.database.models.tool_call import Approval, ToolCall, VerificationResult  # noqa: F401
from voxos.database.models.user import User  # noqa: F401

__all__ = [
    "Base",
    "User",
    "Session",
    "Conversation",
    "Message",
    "Task",
    "TaskStep",
    "ToolCall",
    "Approval",
    "VerificationResult",
    "MemoryEntry",
    "AuditLog",
]
