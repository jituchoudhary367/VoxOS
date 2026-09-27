"""
Plain-language explanations of LLM failures.

"Internal error" hid the real cause: every Groq key had hit its per-minute
or per-day limit and the local fallback model (Ollama) is not installed.
When the user then asked "which internal error?", chat invented "a technical
hiccup". These messages go into the reply (and so into the conversation),
so a follow-up question gets a true answer.
"""

from __future__ import annotations

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

from voxos.llm.key_pool import retry_after_seconds


def _when(seconds: float) -> str:
    if seconds >= 90:
        return f"about {round(seconds / 60)} minutes"
    return "a minute" if seconds > 30 else "a few seconds"


def explain_llm_error(error: BaseException) -> str:
    text = str(error)
    if isinstance(error, RateLimitError):
        wait = _when(retry_after_seconds(error, default=60))
        if "per day" in text or "(TPD)" in text or "(RPD)" in text:
            return (
                "My AI quota is used up: all Groq keys hit their daily limit. "
                f"It frees up in {wait}; adding another key raises the limit."
            )
        return f"My AI service is busy (Groq per-minute limit on all keys). Try again in {wait}."
    if isinstance(error, AuthenticationError):
        return "My AI keys were rejected by Groq; check the keys in .env."
    if isinstance(error, APIConnectionError):
        return "I can't reach the AI service right now; check the internet connection."
    if isinstance(error, APIStatusError):
        return f"The AI service returned an error ({error.status_code})."
    return f"Something broke on my side ({type(error).__name__})."


def is_llm_error(error: BaseException) -> bool:
    return isinstance(error, (RateLimitError, AuthenticationError, APIConnectionError, APIStatusError))
