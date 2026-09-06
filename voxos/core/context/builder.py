"""
Context Builder.

Prepares the execution context for agents by aggregating:
1. Current system state (time, OS, resources)
2. Relevant memory entries
3. Conversation history
4. Active tasks/goals
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from voxos.core.memory.manager import get_memory_manager
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class ContextBuilder:
    """Builds rich context blocks for LLM prompts."""

    def __init__(self) -> None:
        self._memory = get_memory_manager()

    async def build_agent_context(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
        include_memory: bool = True,
        recent_turns: int = 5,
    ) -> str:
        """
        Build the unified context block for an agent.
        
        This block should be prepended or appended to the system prompt.
        """
        now = datetime.now(timezone.utc)
        
        context_parts = [
            "=== SYSTEM CONTEXT ===",
            f"Current UTC Time: {now.isoformat()}",
            f"Current Local Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        ]

        if include_memory and user_id:
            try:
                memories = await self._memory.get_relevant_memories(user_id=user_id, limit=5)
                if memories:
                    context_parts.append("\n=== RELEVANT MEMORY ===")
                    for m in memories:
                        context_parts.append(f"- {m.content}")
            except Exception as e:
                log.warning("context_memory_fetch_error", error=str(e))

        # We could also fetch conversation history from DB here if needed
        # (Usually handled by the chat route passing history in)

        return "\n".join(context_parts)

    def get_system_context(self) -> dict[str, Any]:
        """Get structured system context."""
        import platform
        return {
            "os": platform.system(),
            "time": datetime.now(timezone.utc).isoformat(),
        }


# Singleton
_context_builder: ContextBuilder | None = None

def get_context_builder() -> ContextBuilder:
    global _context_builder
    if _context_builder is None:
        _context_builder = ContextBuilder()
    return _context_builder
