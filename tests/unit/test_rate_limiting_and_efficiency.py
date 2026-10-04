"""Client-side rate limiting, and the worker spending fewer tokens per step."""

from __future__ import annotations

import pytest

from voxos.llm.base import LLMConfig, LLMMessage
from voxos.llm.key_pool import KeyPoolProvider
from voxos.llm.rate_limiter import TokenWindow, estimate_tokens
from voxos.orchestration.worker import _unchanged_read

from tests.unit.test_key_pool import FakeClock, FakeProvider


# ── TokenWindow ──────────────────────────────────────────────────────────────

def test_window_fits_until_the_minute_budget_is_used():
    w = TokenWindow(tokens_per_minute=1000, requests_per_minute=10)
    assert w.fits("m", 600, now=0)
    w.book("m", 600, now=0)
    assert not w.fits("m", 600, now=1)
    assert w.fits("m", 300, now=1)
    assert w.fits("other-model", 900, now=1)  # limits are per model
    assert w.fits("m", 600, now=60)           # the booking aged out


def test_window_says_when_room_frees_up():
    w = TokenWindow(tokens_per_minute=1000, requests_per_minute=10)
    w.book("m", 500, now=0)
    w.book("m", 400, now=10)
    assert w.free_at("m", 600, now=12) == 60.0   # after the first booking leaves


def test_request_cap_counts_too():
    w = TokenWindow(tokens_per_minute=10**6, requests_per_minute=2)
    w.book("m", 1, now=0)
    w.book("m", 1, now=1)
    assert not w.fits("m", 1, now=2)


def test_a_request_bigger_than_the_budget_still_gets_an_empty_key():
    assert TokenWindow(1000, 10).fits("m", 5000, now=0)


def test_settle_replaces_the_estimate():
    w = TokenWindow(1000, 10)
    booking = w.book("m", 900, now=0)
    TokenWindow.settle(booking, 120)
    assert w.used("m", now=1) == (120, 1)


def test_estimate_counts_prompt_and_part_of_the_reply_cap():
    msgs = [LLMMessage(role="user", content="x" * 3500)]
    assert 1000 + 500 <= estimate_tokens(msgs, None, max_tokens=1000) <= 1000 + 500 + 20


# ── the pool paces instead of collecting 429s ────────────────────────────────

CONFIG = LLMConfig(model="m", max_tokens=200)


def _msgs(chars: int) -> list[LLMMessage]:
    return [LLMMessage(role="user", content="x" * chars)]


async def test_busy_key_is_skipped_for_one_with_room():
    clock = FakeClock()
    a, b = FakeProvider("a"), FakeProvider("b")
    pool = KeyPoolProvider([a, b], clock=clock, sleep=clock.sleep, tokens_per_minute=2000)
    first = await pool.complete(_msgs(5000), CONFIG)
    second = await pool.complete(_msgs(5000), CONFIG)
    assert {first.content, second.content} == {"a", "b"}
    assert clock.slept == []


async def test_full_pool_waits_for_room_rather_than_calling():
    clock = FakeClock()
    a = FakeProvider("a")
    a._remaining = None
    pool = KeyPoolProvider([a], clock=clock, sleep=clock.sleep, tokens_per_minute=2000, max_wait_s=90)
    # FakeProvider reports 10 tokens used; book a big estimate by not settling: use a huge prompt
    await pool.complete(_msgs(5000), CONFIG)       # settles to 10 tokens
    pool._slots[0].window.book("m", 1990, clock())  # someone else used the rest of the minute
    await pool.complete(_msgs(5000), CONFIG)
    assert clock.slept and clock.slept[0] > 50       # waited for the window, no 429 involved
    assert pool.paced == 1


async def test_pacing_is_off_without_limits():
    clock = FakeClock()
    pool = KeyPoolProvider([FakeProvider("a")], clock=clock, sleep=clock.sleep)
    for _ in range(5):
        await pool.complete(_msgs(50_000), CONFIG)
    assert clock.slept == []


# ── worker: don't re-read an unchanged page ──────────────────────────────────

def _obs(tool, status="SUCCESS", **args):
    return {"tool": tool, "status": status, "arguments": args}


def test_reread_with_nothing_done_since_is_caught():
    obs = [_obs("browser.open", url="https://amazon.in/s?k=x"), _obs("browser.extract")]
    assert _unchanged_read(obs, {}) == 2
    assert _unchanged_read(obs + [_obs("browser.screenshot")], {"selector": "body"}) == 2


def test_reread_after_a_click_or_with_another_selector_is_allowed():
    obs = [_obs("browser.extract"), _obs("browser.click", selector="a")]
    assert _unchanged_read(obs, {}) is None
    assert _unchanged_read([_obs("browser.extract")], {"selector": "div.price"}) is None
    assert _unchanged_read([_obs("browser.extract", status="FAILED")], {}) is None


def test_router_turns_pacing_on_from_settings():
    from voxos.config import LLMSettings
    from voxos.llm.router import build_primary_provider

    pool = build_primary_provider(LLMSettings(_env_file=None, GROQ_API_KEY="gsk_x", key_tpm=7500, key_rpm=28))
    assert pool._slots[0].window.tokens_per_minute == 7500
    off = build_primary_provider(LLMSettings(_env_file=None, GROQ_API_KEY="gsk_x", key_tpm=0, key_rpm=0))
    assert off._slots[0].window is None


@pytest.mark.parametrize("url", [
    "https://aax-eu-zaz.amazon.in/x/c/abc", "https://www.amazon.in/sspa/click?x", "https://www.amazon.in/gp/slredirect/x",
])
def test_ad_redirect_pattern_matches(url):
    import re

    from voxos.tools.browser.tools import _MAIN_LINKS_JS

    js_regex = re.search(r"/(\\/\\/aax-.*?googleadservices)/", _MAIN_LINKS_JS).group(1)
    assert re.search(js_regex.replace("\\\\", "\\").replace("\\/", "/"), url)
