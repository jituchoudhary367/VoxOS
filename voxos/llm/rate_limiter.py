"""
Client-side rate limiting for one API key: stay under the provider's
per-minute limits instead of discovering them through 429s.

Groq's free tier allows each key roughly 8,000 tokens and 30 requests per
minute, per model. Hitting that returns a 429 and parks the key for up to a
minute, and when all keys were parked at once a request crashed. A
TokenWindow keeps the last 60 seconds of calls per model and answers two
questions: does a request of ~N tokens fit right now, and if not, when will
it? The key pool then sends each call to a key with room, or waits just
long enough, never blindly.

A call is booked with its *estimated* size when it starts (so parallel
workers don't all pick the same "empty" key) and settled with the real
usage when the response arrives.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

WINDOW_S = 60.0
# Rough chars-per-token for English/JSON prompts on Llama/GPT-style
# tokenizers; deliberately on the cautious side.
CHARS_PER_TOKEN = 3.5
# Groq charges the reply too; the cap is max_tokens, typical replies are
# far shorter. Book this much of the cap until the real count is known.
REPLY_SHARE = 0.5


def estimate_tokens(messages: list[Any], system_prompt: str | None, max_tokens: int) -> int:
    """A cautious guess at a call's total tokens, before it is made."""
    chars = len(system_prompt or "")
    for m in messages:
        content = getattr(m, "content", m)
        if isinstance(content, list):  # multimodal parts: count the text, a flat cost per image
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chars += len(str(part.get("text") or ""))
                else:
                    chars += 3000
        else:
            chars += len(str(content or ""))
    return int(chars / CHARS_PER_TOKEN) + int(max(0, max_tokens) * REPLY_SHARE) + 8


@dataclass
class _Booking:
    at: float
    tokens: int


@dataclass
class TokenWindow:
    """The last minute of calls on one key, per model."""

    tokens_per_minute: int
    requests_per_minute: int
    _calls: dict[str, deque[_Booking]] = field(default_factory=dict)

    def _live(self, model: str, now: float) -> deque[_Booking]:
        calls = self._calls.setdefault(model, deque())
        while calls and now - calls[0].at >= WINDOW_S:
            calls.popleft()
        return calls

    def used(self, model: str, now: float) -> tuple[int, int]:
        calls = self._live(model, now)
        return sum(b.tokens for b in calls), len(calls)

    def fits(self, model: str, need: int, now: float) -> bool:
        tokens, requests = self.used(model, now)
        if requests >= self.requests_per_minute:
            return False
        if tokens == 0:
            return True  # a request bigger than the whole budget still gets an empty key
        return tokens + need <= self.tokens_per_minute

    def free_at(self, model: str, need: int, now: float) -> float:
        """The earliest time a request of `need` tokens fits on this key."""
        calls = list(self._live(model, now))
        tokens = sum(b.tokens for b in calls)
        for i, booking in enumerate(calls):
            if self.fits_after(calls[i + 1:], tokens - booking.tokens, need):
                return booking.at + WINDOW_S
            tokens -= booking.tokens
        return now

    def fits_after(self, remaining: list[_Booking], tokens: int, need: int) -> bool:
        if len(remaining) >= self.requests_per_minute:
            return False
        return tokens == 0 or tokens + need <= self.tokens_per_minute

    def book(self, model: str, tokens: int, now: float) -> _Booking:
        booking = _Booking(now, max(0, tokens))
        self._live(model, now).append(booking)
        return booking

    @staticmethod
    def settle(booking: _Booking, actual_tokens: int) -> None:
        """Replace the estimate with what the provider actually counted."""
        if actual_tokens > 0:
            booking.tokens = actual_tokens
