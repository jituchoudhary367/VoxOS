"""
OpenAI-compatible LLM provider.

Works with: OpenAI, Groq, Ollama (openai-compat mode), Together AI,
            Anyscale, any endpoint following the OpenAI chat API spec.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from voxos.llm.base import LLMConfig, LLMMessage, LLMProvider, LLMResponse
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


class OpenAICompatibleProvider(LLMProvider):
    """
    Provider for any OpenAI-compatible chat completions endpoint.

    Configure via:
        base_url: API base URL (e.g., https://api.groq.com/openai/v1)
        api_key:  API key for authentication
        name:     Identifier (e.g., 'groq', 'openai', 'ollama')
    """

    def __init__(self, base_url: str, api_key: str, provider_name: str = "openai") -> None:
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "ollama",  # ollama doesn't need a key
            timeout=60.0,
            max_retries=0,  # We handle retries via tenacity
        )
        self._name = provider_name

    @property
    def name(self) -> str:
        return self._name

    @retry(
        retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[LLMMessage],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send chat completion request with automatic retry on transient errors."""
        openai_messages = self._to_openai_messages(messages, config.system_prompt)

        kwargs: dict = {
            "model": config.model,
            "messages": openai_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        if config.tools:
            kwargs["tools"] = config.tools
            if config.tool_choice:
                kwargs["tool_choice"] = config.tool_choice

        if config.response_format:
            kwargs["response_format"] = config.response_format

        log.debug(
            "llm_request",
            provider=self._name,
            model=config.model,
            message_count=len(openai_messages),
        )

        async with metrics.async_timer("llm.latency", provider=self._name, model=config.model):
            response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        result = LLMResponse(
            content=message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
        )

        metrics.increment("llm.requests", provider=self._name, model=config.model)
        metrics.increment(
            "llm.tokens",
            value=result.total_tokens,
            provider=self._name,
        )

        log.debug(
            "llm_response",
            provider=self._name,
            model=config.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            finish_reason=result.finish_reason,
        )

        return result

    async def stream(
        self,
        messages: list[LLMMessage],
        config: LLMConfig,
    ) -> AsyncIterator[str]:
        """Stream response tokens."""
        openai_messages = self._to_openai_messages(messages, config.system_prompt)

        stream = await self._client.chat.completions.create(
            model=config.model,
            messages=openai_messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def health_check(self) -> bool:
        """Check provider availability with a minimal request."""
        try:
            await self._client.models.list()
            return True
        except Exception as e:
            log.warning("llm_health_check_failed", provider=self._name, error=str(e))
            return False

    @staticmethod
    def _to_openai_messages(
        messages: list[LLMMessage],
        system_prompt: str | None,
    ) -> list[dict]:
        """Convert LLMMessage list to OpenAI API format."""
        result = []

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        for msg in messages:
            m: dict = {"role": msg.role, "content": msg.content}
            if msg.name:
                m["name"] = msg.name
            if msg.tool_calls:
                m["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            result.append(m)

        return result
