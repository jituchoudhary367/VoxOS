"""
Token-aware context construction for LLM calls.

Builds and truncates context windows to stay within model token limits.
"""

from __future__ import annotations

import json
from typing import Any

from voxos.llm.base import LLMMessage
from voxos.observability.logger import get_logger

log = get_logger(__name__)

# Approximate chars-per-token ratio (conservative)
CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    """Rough token count estimate without a tokenizer."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def truncate_text(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens tokens."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    # Truncate and append indicator
    return text[:max_chars - 20] + "\n...[truncated]"


def build_context_window(
    messages: list[LLMMessage],
    max_tokens: int = 4096,
    system_tokens_reserved: int = 500,
) -> list[LLMMessage]:
    """
    Trim a message list to fit within max_tokens.

    Strategy:
    1. Always keep the last (most recent) user message
    2. Keep as many prior messages as fit, oldest dropped first
    3. Truncate individual messages if they are very long
    """
    available = max_tokens - system_tokens_reserved

    # Count tokens from the end (most recent first)
    kept: list[LLMMessage] = []
    used_tokens = 0

    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.content)
        if msg_tokens > available:
            # Truncate this message to fit
            truncated = truncate_text(msg.content, available - used_tokens)
            kept.insert(0, LLMMessage(role=msg.role, content=truncated))
            used_tokens += estimate_tokens(truncated)
            break
        if used_tokens + msg_tokens > available:
            break
        kept.insert(0, msg)
        used_tokens += msg_tokens

    if len(kept) < len(messages):
        log.debug(
            "context_window_truncated",
            original_count=len(messages),
            kept_count=len(kept),
            estimated_tokens=used_tokens,
        )

    return kept


class ContextWindow:
    """
    Manages a sliding context window for LLM conversations.

    Automatically evicts old messages to stay within token budget.
    """

    def __init__(self, max_tokens: int = 4096, system_tokens_reserved: int = 500) -> None:
        self._messages: list[LLMMessage] = []
        self._max_tokens = max_tokens
        self._system_tokens_reserved = system_tokens_reserved

    def add(self, role: str, content: str) -> None:
        """Add a message to the window."""
        self._messages.append(LLMMessage(role=role, content=content))

    def get_messages(self) -> list[LLMMessage]:
        """Return the trimmed message list that fits the token budget."""
        return build_context_window(
            self._messages,
            max_tokens=self._max_tokens,
            system_tokens_reserved=self._system_tokens_reserved,
        )

    def clear(self) -> None:
        """Reset the window."""
        self._messages.clear()

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def estimated_tokens(self) -> int:
        return sum(estimate_tokens(m.content) for m in self._messages)
