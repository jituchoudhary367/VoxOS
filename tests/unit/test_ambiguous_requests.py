"""Regression table for short, ambiguous and follow-up requests.

From a live session: "pause" did nothing, "stop the music" failed, "play
that" tried to open an app with no name, and "search more laptops from
another company" would have opened a new Bing tab for the literal words.
Rules may only take requests whose meaning is certain; everything that
refers to earlier turns or the screen goes to the AI, which sees both.
"""

from __future__ import annotations

import pytest

from voxos.agents.listener.agent import ListenerAgent
from voxos.core.router import ExecutionClass, ExecutionRouter, is_follow_up


# ─── Media commands: certain meaning, instant ───────────────────────────────

@pytest.mark.parametrize(("text", "action"), [
    ("pause", "pause"),
    ("Pause.", "pause"),
    ("pause it", "pause"),
    ("pause the video", "pause"),
    ("pause the music", "pause"),
    ("please pause the song", "pause"),
    ("stop the music", "stop"),
    ("stop the song", "stop"),
    ("stop playing", "stop"),
    ("stop it", "stop"),
    ("resume", "play"),
    ("resume the video", "play"),
    ("continue playing", "play"),
    ("unpause", "play"),
    ("play", "play"),
    ("play again", "play"),
    ("next song", "next"),
    ("skip", "next"),
    ("skip this song", "next"),
    ("play next video", "next"),
    ("mute", "mute"),
    ("mute the video", "mute"),
    ("unmute", "unmute"),
])
def test_media_commands_are_understood_without_the_llm(text: str, action: str) -> None:
    result = ListenerAgent._fast_classify(text)

    assert result is not None, text
    assert result.intent == "media_control"
    assert result.entities == {"action": action}
    route = ExecutionRouter().route(result.intent, result.confidence, result.entities, request_text=text)
    assert route is ExecutionClass.FAST_PATH
    assert ExecutionRouter().fast_path_tool("media_control") == "browser.media"


@pytest.mark.parametrize("text", [
    "play lofi music on youtube",       # a search-and-play, not "resume"
    "play that",                        # "that" = something said earlier
    "play it",                          # same
    "play the song you found",
    "stop the timer",                   # not media
    "stop",                             # too vague: stop what?
    "next",                             # next what?
    "pause the video and tell me the time",  # two jobs
])
def test_lookalikes_are_not_media_commands(text: str) -> None:
    result = ListenerAgent._fast_classify(text)

    assert result is None or result.intent != "media_control", text


# ─── Follow-ups: the AI resolves them, the rules must not ───────────────────

@pytest.mark.parametrize("text", [
    "search more laptops from another company",
    "search more laptop from anther company",
    "search for another one",
    "search that on amazon",
    "search the same thing on flipkart",
    "search it again",
    "search for something else instead",
])
def test_follow_up_searches_skip_the_keyword_shortcut(text: str) -> None:
    assert ListenerAgent._fast_classify(text) is None, text


@pytest.mark.parametrize("text", [
    "search more laptops from another company",
    "play that",
    "try now",
    "do it again",
    "go back to the previous one",
    "close it",
    "open the same on amazon",
])
def test_follow_up_phrasing_is_detected(text: str) -> None:
    assert is_follow_up(text), text


@pytest.mark.parametrize("text", [
    "search google for best laptops",
    "open notepad",
    "how much ram am i using",
])
def test_self_contained_requests_are_not_follow_ups(text: str) -> None:
    assert not is_follow_up(text), text


def test_llm_guess_for_a_follow_up_goes_to_the_agents() -> None:
    # Even when the LLM listener maps it to a deterministic intent with
    # valid-looking arguments, a follow-up needs the context-aware agents.
    route = ExecutionRouter().route(
        "search_web_browser", 0.95, {"query": "laptops from another company"},
        source="llm", request_text="search more laptops from another company",
    )
    assert route is ExecutionClass.REASONING


# ─── Plain searches keep working ────────────────────────────────────────────

@pytest.mark.parametrize(("text", "query", "engine"), [
    ("search google for best laptops", "best laptops", "google"),
    ("search for python tutorials", "python tutorials", "bing"),
])
def test_self_contained_searches_still_use_the_shortcut(text: str, query: str, engine: str) -> None:
    result = ListenerAgent._fast_classify(text)

    assert result is not None
    assert result.entities == {"query": query, "engine": engine}
