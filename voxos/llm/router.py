"""
LLM Router — selects the right model for the task complexity, with fallback support.

Principle: Do NOT call an expensive LLM for simple deterministic tasks.

Routing:
    SIMPLE tasks  → fast model  (Groq llama-3.1-8b-instant or equivalent)
    COMPLEX tasks → strong model (Groq llama-3.3-70b or equivalent)

Usage:
    router = get_llm_router()
    config = router.config_for(TaskComplexity.SIMPLE)
    response = await router.provider.complete(messages, config)
"""

from __future__ import annotations

import asyncio
from enum import Enum
from functools import lru_cache
from typing import AsyncIterator

from voxos.config import get_settings
from voxos.llm.base import LLMConfig, LLMProvider, LLMMessage, LLMResponse
from voxos.llm.openai_compat import OpenAICompatibleProvider
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class TaskComplexity(str, Enum):
    """Task complexity hint for model routing."""

    SIMPLE = "simple"     # Classification, single-tool selection, factual lookup
    STANDARD = "standard" # Multi-step intent, moderate reasoning
    COMPLEX = "complex"   # Planning, decomposition, deep reasoning


class FallbackProvider(LLMProvider):
    """Wraps a primary and fallback provider."""
    
    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def name(self) -> str:
        return f"{self.primary.name}-with-fallback"

    def _get_fallback_config(self, config: LLMConfig) -> LLMConfig:
        """Map Groq model names to OpenRouter meta-llama names if needed."""
        fallback_config = config
        model = config.model.lower()
        if "llama-3" in model and not model.startswith("meta-llama/"):
            if "70b" in model:
                new_model = "meta-llama/llama-3.3-70b-instruct"
            else:
                new_model = "meta-llama/llama-3.1-8b-instruct"
            fallback_config = LLMConfig(**{**config.model_dump(), "model": new_model})
        return fallback_config

    async def complete(self, messages: list[LLMMessage], config: LLMConfig) -> LLMResponse:
        try:
            return await self.primary.complete(messages, config)
        except Exception as e:
            log.warning("primary_llm_failed_using_fallback", error=str(e), fallback=self.fallback.name)
            fallback_config = self._get_fallback_config(config)
            return await self.fallback.complete(messages, fallback_config)

    async def stream(self, messages: list[LLMMessage], config: LLMConfig) -> AsyncIterator[str]:
        try:
            async for chunk in self.primary.stream(messages, config):
                yield chunk
        except Exception as e:
            log.warning("primary_llm_stream_failed_using_fallback", error=str(e), fallback=self.fallback.name)
            fallback_config = self._get_fallback_config(config)
            async for chunk in self.fallback.stream(messages, fallback_config):
                yield chunk

    async def health_check(self) -> bool:
        results = await asyncio.gather(
            self.primary.health_check(),
            self.fallback.health_check(),
            return_exceptions=True,
        )
        return any(result is True for result in results)


class LLMRouter:
    """
    Routes LLM requests to appropriate model based on task complexity.

    Cost-aware: simple tasks use cheap/fast model; only complex reasoning
    uses the stronger model.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        settings = get_settings().llm
        self._fast_model = settings.fast_model
        self._strong_model = settings.strong_model
        self._temperature = settings.temperature
        self._max_tokens = settings.max_tokens

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    def config_for(
        self,
        complexity: TaskComplexity = TaskComplexity.STANDARD,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict | None = None,
        system_prompt: str | None = None,
    ) -> LLMConfig:
        """Return LLMConfig optimized for the given complexity level."""
        if complexity == TaskComplexity.SIMPLE:
            model = self._fast_model
        elif complexity == TaskComplexity.COMPLEX:
            model = self._strong_model
        else:
            model = self._fast_model  # Use fast by default, upgrade only when needed

        config = LLMConfig(
            model=model,
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens or self._max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            system_prompt=system_prompt,
        )

        log.debug("llm_route", complexity=complexity, model=model)
        return config

    async def health_check(self) -> bool:
        return await self._provider.health_check()


@lru_cache(maxsize=1)
def get_llm_router() -> LLMRouter:
    """Return cached LLM router singleton with fallback support."""
    settings = get_settings().llm
    
    primary = OpenAICompatibleProvider(
        base_url=settings.base_url,  # Usually Groq
        api_key=settings.groq_api_key,
        provider_name=settings.provider,
    )
    
    fallback = OpenAICompatibleProvider(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
        provider_name="openrouter",
    )
    
    provider = FallbackProvider(primary, fallback)
    log.info("llm_router_initialized", provider="groq-with-openrouter-fallback", fast=settings.fast_model, strong=settings.strong_model)
    return LLMRouter(provider=provider)
