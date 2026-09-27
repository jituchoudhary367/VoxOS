"""
Key pool — spreads LLM calls over several API keys of the same provider.

Groq rate limits (tokens/min, requests/min, tokens/day) are per account AND
per model, so each extra key adds a full quota for every model. That only
helps if a throttled key is skipped rather than waited on:

- each call goes to the usable key with the fewest calls in flight, then the
  most remaining tokens (as reported by the provider's rate-limit headers);
- a 429 cools that key down FOR THAT MODEL for its retry-after, and the call
  moves to the next key at once (a daily limit on gpt-oss-20b once parked
  whole keys that still had a full gpt-oss-120b quota);
- when a model is exhausted on every key, the call tries the other models
  of its family (`fallback_models`) on the same keys;
- a rejected key (401) is disabled for the rest of the process;
- only when nothing is usable does the call wait, and only up to
  `max_wait_s`; past that the RateLimitError propagates.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from openai import AuthenticationError, RateLimitError

from voxos.llm.base import LLMConfig, LLMMessage, LLMProvider, LLMResponse
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()

DEFAULT_COOLDOWN_S = 10.0


@dataclass
class _Slot:
    index: int
    provider: LLMProvider
    # model -> monotonic time until which this key may not call it
    cooldowns: dict[str, float] = field(default_factory=dict)
    in_flight: int = 0
    disabled: bool = False
    remaining_tokens: int | None = None
    calls: int = 0
    tokens_used: int = 0
    rate_limited: int = 0

    def cooling(self, model: str, now: float) -> bool:
        return self.cooldowns.get(model, 0.0) > now


_TRY_AGAIN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def retry_after_seconds(error: Exception, default: float = DEFAULT_COOLDOWN_S) -> float:
    """The provider's retry-after header, else Groq's "try again in 1m2.5s"."""
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return max(float(header), 0.5)
        except ValueError:
            pass
    match = _TRY_AGAIN.search(str(error))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return default


class KeyPoolProvider(LLMProvider):
    """An LLMProvider over N same-endpoint providers, one per API key."""

    def __init__(
        self,
        providers: list[LLMProvider],
        *,
        max_wait_s: float = 20.0,
        fallback_models: list[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not providers:
            raise ValueError("KeyPoolProvider needs at least one provider")
        self._slots = [_Slot(i, p) for i, p in enumerate(providers)]
        self._max_wait_s = max_wait_s
        # Interchangeable models; a model outside this list (e.g. the vision
        # model) is never swapped for one that cannot do its job.
        self._fallback_models = list(fallback_models or [])
        self._clock = clock
        self._sleep = sleep
        self._turn = 0

    @property
    def name(self) -> str:
        return f"{self._slots[0].provider.name}-pool[{len(self._slots)}]"

    @property
    def size(self) -> int:
        return len(self._slots)

    def _pick(self, model: str) -> _Slot | None:
        now = self._clock()
        usable = [s for s in self._slots if not s.disabled and not s.cooling(model, now)]
        if not usable:
            return None
        turn, n = self._turn, len(self._slots)
        self._turn += 1
        # Fewest in flight, then most quota left (unknown counts as plenty),
        # then round-robin so idle keys share the load evenly.
        return min(
            usable,
            key=lambda s: (
                s.in_flight,
                -(s.remaining_tokens if s.remaining_tokens is not None else 1 << 30),
                (s.index - turn) % n,
            ),
        )

    def _wake_time(self, model: str) -> float | None:
        live = [s for s in self._slots if not s.disabled]
        if not live:
            return None
        return min(s.cooldowns.get(model, 0.0) for s in live)

    async def _next_slot(self, model: str, deadline: float, last_error: Exception | None) -> _Slot:
        while True:
            slot = self._pick(model)
            if slot is not None:
                return slot
            wake = self._wake_time(model)
            if wake is None:
                raise last_error or RuntimeError("Every API key in the pool was rejected")
            if wake > deadline:
                metrics.increment("llm.key_pool.exhausted", model=model)
                raise last_error or RuntimeError("Every API key in the pool is rate limited")
            await self._sleep(max(wake - self._clock(), 0.0))

    def _on_rate_limit(self, slot: _Slot, model: str, error: RateLimitError) -> None:
        wait = retry_after_seconds(error)
        slot.cooldowns[model] = self._clock() + wait
        slot.rate_limited += 1
        metrics.increment("llm.key_pool.rate_limited", key=str(slot.index))
        log.warning("llm_key_rate_limited", key=slot.index, model=model, cooldown_s=round(wait, 1))

    def _on_auth_error(self, slot: _Slot) -> None:
        slot.disabled = True
        log.error("llm_key_rejected", key=slot.index)

    def _models_for(self, model: str) -> list[str]:
        """The requested model first, then its interchangeable siblings."""
        if model not in self._fallback_models:
            return [model]
        return [model] + [m for m in self._fallback_models if m != model]

    async def complete(self, messages: list[LLMMessage], config: LLMConfig) -> LLMResponse:
        last_error: Exception | None = None
        models = self._models_for(config.model)
        for n, model in enumerate(models):
            # Only the last model may wait for a key to cool down; a sibling
            # model with quota left beats waiting out a daily limit.
            immediate = n < len(models) - 1
            deadline = self._clock() + (0.0 if immediate else self._max_wait_s)
            try:
                response = await self._complete_with(model, messages, config, deadline)
            except RateLimitError as e:
                last_error = e
                if n + 1 < len(models):
                    log.warning("llm_model_exhausted_falling_back", model=model, next_model=models[n + 1])
                continue
            if model != config.model:
                metrics.increment("llm.key_pool.model_fallback", model=model)
            return response
        assert last_error is not None
        raise last_error

    async def _complete_with(
        self, model: str, messages: list[LLMMessage], config: LLMConfig, deadline: float
    ) -> LLMResponse:
        if model != config.model:
            config = dataclasses.replace(config, model=model)
        last_error: Exception | None = None
        while True:
            slot = await self._next_slot(model, deadline, last_error)
            slot.in_flight += 1
            try:
                response = await slot.provider.complete(messages, config)
            except RateLimitError as e:
                last_error = e
                self._on_rate_limit(slot, model, e)
                continue
            except AuthenticationError as e:
                last_error = e
                self._on_auth_error(slot)
                continue
            finally:
                slot.in_flight -= 1
            slot.calls += 1
            slot.tokens_used += response.total_tokens
            state: dict[str, Any] = getattr(slot.provider, "rate_limit_state", None) or {}
            if state.get("remaining_tokens") is not None:
                slot.remaining_tokens = int(state["remaining_tokens"])
            return response

    async def stream(self, messages: list[LLMMessage], config: LLMConfig) -> AsyncIterator[str]:
        model = config.model
        deadline = self._clock() + self._max_wait_s
        last_error: Exception | None = None
        while True:
            slot = await self._next_slot(model, deadline, last_error)
            started = False
            slot.in_flight += 1
            try:
                async for chunk in slot.provider.stream(messages, config):
                    started = True
                    yield chunk
                slot.calls += 1
                return
            except RateLimitError as e:
                if started:
                    raise
                last_error = e
                self._on_rate_limit(slot, model, e)
            except AuthenticationError as e:
                if started:
                    raise
                last_error = e
                self._on_auth_error(slot)
            finally:
                slot.in_flight -= 1

    async def health_check(self) -> bool:
        live = [s.provider for s in self._slots if not s.disabled]
        results = await asyncio.gather(*(p.health_check() for p in live), return_exceptions=True)
        return any(r is True for r in results)

    def stats(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [
            {
                "key": s.index,
                "disabled": s.disabled,
                "cooling_s": {m: round(t - now, 1) for m, t in s.cooldowns.items() if t > now},
                "in_flight": s.in_flight,
                "remaining_tokens": s.remaining_tokens,
                "calls": s.calls,
                "tokens_used": s.tokens_used,
                "rate_limited": s.rate_limited,
            }
            for s in self._slots
        ]
