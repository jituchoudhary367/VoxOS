"""KeyPoolProvider spreads LLM calls over several API keys (one per account).

Rate limits are per account, so N keys give N times the throughput — but only
if a throttled key is skipped instead of waited on.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from openai import AuthenticationError, RateLimitError

from voxos.config import LLMSettings
from voxos.llm.base import LLMConfig, LLMMessage, LLMProvider, LLMResponse
from voxos.llm.key_pool import KeyPoolProvider

CONFIG = LLMConfig(model="m")
MESSAGES = [LLMMessage(role="user", content="hi")]


def _rate_limit(retry_after: str = "5") -> RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, headers={"retry-after": retry_after}, request=request)
    return RateLimitError("rate limited", response=response, body=None)


def _auth_error() -> AuthenticationError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return AuthenticationError("invalid key", response=response, body=None)


class FakeProvider(LLMProvider):
    def __init__(self, name: str, errors: list[Exception] | None = None, remaining: int | None = None):
        self._name = name
        self.errors = list(errors or [])
        self.calls = 0
        self.rate_limit_state: dict[str, int] = {}
        self._remaining = remaining
        self.gate: asyncio.Event | None = None

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, messages, config) -> LLMResponse:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.errors:
            raise self.errors.pop(0)
        if self._remaining is not None:
            self.rate_limit_state = {"remaining_tokens": self._remaining}
        return LLMResponse(content=self._name, model=config.model, usage={"total_tokens": 10})

    async def stream(self, messages, config):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        yield self._name

    async def health_check(self) -> bool:
        return True


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_rate_limited_key_fails_over_to_next_key_without_waiting():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("30")])
    b = FakeProvider("b")
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep)

    response = await pool.complete(MESSAGES, CONFIG)

    assert response.content == "b"
    assert clock.slept == []


async def test_cooling_key_is_skipped_until_retry_after_passes():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("30")])
    b = FakeProvider("b")
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep)

    await pool.complete(MESSAGES, CONFIG)  # a throttled, b answers
    a_calls = a.calls
    for _ in range(3):
        assert (await pool.complete(MESSAGES, CONFIG)).content == "b"
    assert a.calls == a_calls

    clock.now += 31
    contents = {(await pool.complete(MESSAGES, CONFIG)).content for _ in range(4)}
    assert "a" in contents


async def test_all_keys_throttled_waits_for_earliest_cooldown():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("8")])
    b = FakeProvider("b", errors=[_rate_limit("3")])
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep, max_wait_s=20)

    response = await pool.complete(MESSAGES, CONFIG)

    assert response.content == "b"
    assert clock.slept == [pytest.approx(3.0)]


async def test_all_keys_throttled_beyond_max_wait_raises_rate_limit():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("60")])
    b = FakeProvider("b", errors=[_rate_limit("60")])
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep, max_wait_s=20)

    with pytest.raises(RateLimitError):
        await pool.complete(MESSAGES, CONFIG)


async def test_invalid_key_is_disabled_and_others_keep_working():
    clock = FakeClock()
    bad = FakeProvider("bad", errors=[_auth_error()])
    good = FakeProvider("good")
    pool = KeyPoolProvider([bad, good], clock=clock, sleep=clock.sleep)

    for _ in range(4):
        assert (await pool.complete(MESSAGES, CONFIG)).content == "good"
    assert bad.calls == 1
    assert pool.stats()[0]["disabled"] is True


async def test_concurrent_calls_spread_over_idle_keys():
    a, b, c = FakeProvider("a"), FakeProvider("b"), FakeProvider("c")
    gate = asyncio.Event()
    for p in (a, b, c):
        p.gate = gate
    pool = KeyPoolProvider([a, b, c])

    calls = [asyncio.create_task(pool.complete(MESSAGES, CONFIG)) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*calls)

    assert sorted(r.content for r in results) == ["a", "b", "c"]


async def test_prefers_key_with_most_remaining_tokens():
    low = FakeProvider("low", remaining=100)
    high = FakeProvider("high", remaining=7000)
    pool = KeyPoolProvider([low, high])
    await pool.complete(MESSAGES, CONFIG)
    await pool.complete(MESSAGES, CONFIG)  # both keys now report their quota

    assert (await pool.complete(MESSAGES, CONFIG)).content == "high"


async def test_stream_fails_over_on_rate_limit():
    a = FakeProvider("a", errors=[_rate_limit("30")])
    b = FakeProvider("b")
    pool = KeyPoolProvider([a, b])

    chunks = [chunk async for chunk in pool.stream(MESSAGES, CONFIG)]

    assert chunks == ["b"]


def test_settings_collect_every_groq_key_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no real .env
    monkeypatch.setenv("GROQ_API_KEYS", "k1, k2 ,k3")
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY_2", "k4")

    assert LLMSettings(_env_file=None).all_groq_keys() == ["k1", "k2", "k3", "k4"]


def test_settings_single_key_still_works(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no real .env
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "only")

    assert LLMSettings(_env_file=None).all_groq_keys() == ["only"]


def test_retry_after_reads_groq_message_when_header_missing():
    from voxos.llm.key_pool import retry_after_seconds

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = RateLimitError(
        "Rate limit reached. Please try again in 1m2.5s.",
        response=httpx.Response(429, request=request),
        body=None,
    )
    assert retry_after_seconds(error) == pytest.approx(62.5)


def test_router_pools_every_configured_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no real .env
    from voxos.llm.router import build_primary_provider

    monkeypatch.setenv("GROQ_API_KEYS", "k1,k2,k3")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)

    primary = build_primary_provider(LLMSettings(_env_file=None))

    assert isinstance(primary, KeyPoolProvider)
    assert primary.size == 3


async def test_provider_records_quota_from_response_headers():
    from voxos.llm.openai_compat import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider("http://localhost:1/v1", "k", retry_rate_limits=False)

    class Raw:
        headers = {"x-ratelimit-remaining-tokens": "5120", "x-ratelimit-remaining-requests": "999"}

        def parse(self):
            from types import SimpleNamespace as NS

            message = NS(content="ok", tool_calls=None)
            return NS(
                choices=[NS(message=message, finish_reason="stop")],
                model="m",
                usage=NS(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class Completions:
        class with_raw_response:
            @staticmethod
            async def create(**kwargs):
                return Raw()

    class Chat:
        completions = Completions()

    class Client:
        chat = Chat()

    provider._client = Client()
    response = await provider.complete(MESSAGES, CONFIG)

    assert response.content == "ok"
    assert provider.rate_limit_state == {"remaining_tokens": 5120, "remaining_requests": 999}


def test_settings_read_any_numbered_groq_key(monkeypatch, tmp_path):
    for name in ("GROQ_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "GROQ_API_KEY_10"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=k1\nGROQ_API_KEY_2=k2\nGROQ_API_KEY_10=k10\nGROQ_API_KEY_3=k3\n")
    monkeypatch.setenv("GROQ_API_KEY_4", "k4")

    settings = LLMSettings(_env_file=str(env_file))

    assert settings.all_groq_keys(env_files=[str(env_file)]) == ["k1", "k2", "k3", "k4", "k10"]


# ─── Limits are per model: live, a daily limit on gpt-oss-20b parked whole keys ──

def _tpd_error(retry: str = "900") -> RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, headers={"retry-after": retry}, request=request)
    return RateLimitError("Rate limit reached ... tokens per day (TPD)", response=response, body=None)


class ModelAwareProvider(FakeProvider):
    """Rate limits one model, answers for others."""

    def __init__(self, name, exhausted: set[str]):
        super().__init__(name)
        self.exhausted = exhausted
        self.models: list[str] = []

    async def complete(self, messages, config):
        self.models.append(config.model)
        if config.model in self.exhausted:
            raise _tpd_error()
        return LLMResponse(content=f"{self._name}:{config.model}", model=config.model, usage={"total_tokens": 1})


async def test_a_daily_limit_on_one_model_leaves_the_key_usable_for_another():
    clock = FakeClock()
    a = ModelAwareProvider("a", exhausted={"small"})
    pool = KeyPoolProvider([a], clock=clock, sleep=clock.sleep)

    with pytest.raises(RateLimitError):
        await pool.complete(MESSAGES, LLMConfig(model="small"))
    response = await pool.complete(MESSAGES, LLMConfig(model="big"))

    assert response.content == "a:big"


async def test_exhausted_model_falls_back_to_the_other_model_on_the_same_keys():
    clock = FakeClock()
    a = ModelAwareProvider("a", exhausted={"small"})
    b = ModelAwareProvider("b", exhausted={"small"})
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep, fallback_models=["small", "big"])

    response = await pool.complete(MESSAGES, LLMConfig(model="small"))

    assert response.content.endswith(":big")
    assert clock.slept == []  # no waiting out a 15-minute daily limit


async def test_models_outside_the_fallback_family_never_switch():
    """The vision model must not silently become a text-only model."""
    clock = FakeClock()
    a = ModelAwareProvider("a", exhausted={"vision"})
    pool = KeyPoolProvider([a], clock=clock, sleep=clock.sleep, fallback_models=["small", "big"])

    with pytest.raises(RateLimitError):
        await pool.complete(MESSAGES, LLMConfig(model="vision"))
    assert a.models == ["vision"]


async def test_fallback_to_local_model_that_is_not_running_reports_the_quota_error():
    """Ollama is not installed: the user must hear "quota", not "connection error"."""
    from openai import APIConnectionError

    from voxos.llm.router import FallbackProvider

    class Down(FakeProvider):
        async def complete(self, messages, config):
            raise APIConnectionError(request=httpx.Request("POST", "http://localhost:11434"))

    quota = FakeProvider("groq", errors=[_tpd_error()])
    provider = FallbackProvider(quota, Down("ollama"))

    with pytest.raises(RateLimitError):
        await provider.complete(MESSAGES, CONFIG)


@pytest.mark.parametrize(("error_text", "expected"), [
    ("Rate limit reached for model `openai/gpt-oss-20b` on tokens per day (TPD): Limit 200000. Please try again in 16m5.2s.",
     "daily"),
    ("Rate limit reached on tokens per minute (TPM): Limit 8000. Please try again in 25.2s.", "minute"),
])
def test_quota_errors_are_explained_plainly(error_text, expected):
    from voxos.llm.errors import explain_llm_error

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = RateLimitError(error_text, response=httpx.Response(429, request=request), body=None)

    message = explain_llm_error(error)

    assert expected in message
    assert "Internal error" not in message


# ── keys already cooling when a call starts ──────────────────────────────────
# Regression: the pool raised a bare RuntimeError("Every API key in the pool is
# rate limited") when every key was already resting on the first model before
# this call had tried anything. Model fallback and the Ollama fallback only
# catch RateLimitError, so the worker crashed: "Something broke on my side
# (RuntimeError)".

async def test_keys_already_cooling_fall_back_to_the_sibling_model():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("60")])
    b = FakeProvider("b", errors=[_rate_limit("60")])
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep, fallback_models=["m", "m2"])

    first = await pool.complete(MESSAGES, CONFIG)       # both keys throttled on m → m2 answers
    assert first.model == "m2"
    second = await pool.complete(MESSAGES, CONFIG)      # keys still resting on m: go straight to m2
    assert second.model == "m2"


async def test_exhausted_pool_raises_a_rate_limit_error_not_runtime_error():
    clock = FakeClock()
    a = FakeProvider("a", errors=[_rate_limit("600")])
    pool = KeyPoolProvider([a], clock=clock, sleep=clock.sleep, max_wait_s=5)

    with pytest.raises(RateLimitError):
        await pool.complete(MESSAGES, CONFIG)
    with pytest.raises(RateLimitError):                 # a fresh call, nothing tried yet
        await pool.complete(MESSAGES, CONFIG)
