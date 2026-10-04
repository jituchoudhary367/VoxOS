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
- with per-key limits set, each key keeps a one-minute window of what it
  sent (see rate_limiter.py): a call only goes to a key with room for its
  estimated size, and otherwise waits for the earliest room instead of
  collecting 429s;
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
from voxos.llm.rate_limiter import TokenWindow, estimate_tokens
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
    window: TokenWindow | None = None

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


def _pool_error(status: int, message: str, retry_after: float = 0.0) -> RateLimitError | AuthenticationError:
    """The error a single key would have raised, for a pool that tried none."""
    import httpx

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    if status == 429:
        headers = {"retry-after": str(max(1, round(retry_after)))}
        return RateLimitError(message, response=httpx.Response(429, headers=headers, request=request), body=None)
    return AuthenticationError(message, response=httpx.Response(401, request=request), body=None)


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
        tokens_per_minute: int | None = None,
        requests_per_minute: int | None = None,
    ) -> None:
        if not providers:
            raise ValueError("KeyPoolProvider needs at least one provider")
        self._slots = [_Slot(i, p) for i, p in enumerate(providers)]
        if tokens_per_minute or requests_per_minute:
            for slot in self._slots:
                slot.window = TokenWindow(tokens_per_minute or 1 << 30, requests_per_minute or 1 << 30)
        self._max_wait_s = max_wait_s
        # Interchangeable models; a model outside this list (e.g. the vision
        # model) is never swapped for one that cannot do its job.
        self._fallback_models = list(fallback_models or [])
        self._clock = clock
        self._sleep = sleep
        self.paced = 0  # calls that waited for room instead of risking a 429
        self._turn = 0

    @property
    def name(self) -> str:
        return f"{self._slots[0].provider.name}-pool[{len(self._slots)}]"

    @property
    def size(self) -> int:
        return len(self._slots)

    def _pick(self, model: str, need: int = 0) -> _Slot | None:
        now = self._clock()
        usable = [
            s for s in self._slots
            if not s.disabled and not s.cooling(model, now)
            and (s.window is None or s.window.fits(model, need, now))
        ]
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

    def _wake_time(self, model: str, need: int = 0) -> float | None:
        live = [s for s in self._slots if not s.disabled]
        if not live:
            return None
        now = self._clock()
        return min(
            max(s.cooldowns.get(model, 0.0), s.window.free_at(model, need, now) if s.window else 0.0)
            for s in live
        )

    async def _next_slot(
        self, model: str, deadline: float, last_error: Exception | None, need: int = 0
    ) -> _Slot:
        while True:
            slot = self._pick(model, need)
            if slot is not None:
                return slot
            wake = self._wake_time(model, need)
            if wake is None:
                raise last_error or _pool_error(401, "Every API key in the pool was rejected")
            if wake > deadline:
                metrics.increment("llm.key_pool.exhausted", model=model)
                # Raised as a real 429 even when this call tried nothing (every
                # key was already resting): model fallback, the Ollama fallback
                # and the user's "busy, try again in…" message all key off it.
                raise last_error or _pool_error(
                    429, f"Every API key in the pool is rate limited for {model}",
                    retry_after=wake - self._clock(),
                )
            metrics.increment("llm.key_pool.paced", model=model)
            self.paced += 1
            log.info("llm_key_pool_pacing", model=model, wait_s=round(max(wake - self._clock(), 0.0), 2))
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
        need = estimate_tokens(messages, config.system_prompt, config.max_tokens)
        for n, model in enumerate(models):
            # Only the last model may wait for a key to cool down; a sibling
            # model with quota left beats waiting out a daily limit.
            immediate = n < len(models) - 1
            deadline = self._clock() + (0.0 if immediate else self._max_wait_s)
            try:
                response = await self._complete_with(model, messages, config, deadline, need)
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
        self, model: str, messages: list[LLMMessage], config: LLMConfig, deadline: float, need: int = 0
    ) -> LLMResponse:
        if model != config.model:
            config = dataclasses.replace(config, model=model)
        last_error: Exception | None = None
        while True:
            slot = await self._next_slot(model, deadline, last_error, need)
            slot.in_flight += 1
            booking = slot.window.book(model, need, self._clock()) if slot.window else None
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
            if booking is not None:
                TokenWindow.settle(booking, response.total_tokens)
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
                "minute_tokens": (
                    sum(s.window.used(m, now)[0] for m in list(s.window._calls)) if s.window else None
                ),
            }
            for s in self._slots
        ]
