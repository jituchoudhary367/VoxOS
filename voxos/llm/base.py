"""
LLM Provider Abstract Base Class.

All LLM providers must implement this interface.
This allows swapping between OpenAI, Groq, Ollama, Anthropic etc.
without changing agent code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class LLMMessage:
    """A single message in a chat conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    """Response from an LLM call."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return self.usage.get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


@dataclass
class LLMConfig:
    """Configuration for a single LLM call."""

    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, str] | None = None  # {"type": "json_object"}
    system_prompt: str | None = None


class LLMProvider(ABC):
    """
    Abstract base for LLM providers.

    Implement this to add support for a new LLM backend.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'groq', 'openai', 'ollama')."""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        config: LLMConfig,
    ) -> LLMResponse:
        """
        Send messages and return a complete response.

        Args:
            messages: Conversation history (system, user, assistant turns)
            config: Model and generation parameters

        Returns:
            LLMResponse with content, token usage, and any tool calls
        """

    @abstractmethod
    async def stream(
        self,
        messages: list[LLMMessage],
        config: LLMConfig,
    ) -> AsyncIterator[str]:
        """
        Stream response tokens as they arrive.

        Yields:
            Token strings as they are generated
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and functional."""
