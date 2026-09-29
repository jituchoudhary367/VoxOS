"""Battery/CPU questions reach system.get_resources and are answered specifically.

Regression: "What's my battery level?" was answered with the OS name and
uptime, and "i mean what is my battery percentage" with CPU/RAM/disk but no
battery. Punctuation is stripped before the fast path, so "what's" arrived as
"whats" and missed; the misses fell to the LLM, whose intent examples listed
"what's my RAM" and "CPU usage" under get_system_info.
"""

from __future__ import annotations

import pytest

from voxos.agents.listener.agent import ListenerAgent
from voxos.core.conversation.service import ConversationService


@pytest.mark.parametrize("text", [
    "What's my battery level?",
    "i mean what is my battery percentage",
    "battery percentage?",
    "how's my cpu doing?",
    "how much battery is left",
    "is my laptop charging",
    "battery kitni hai",
    "RAM usage",
    "system stats",
])
def test_resource_questions_take_the_fast_path(text: str) -> None:
    result = ListenerAgent._fast_classify(text)
    assert result is not None and result.intent == "get_resources"


@pytest.mark.parametrize("text", [
    "open disk cleanup",
    "close task manager to free memory",
    "hello",
    "what's the time",
])
def test_non_questions_are_not_resource_requests(text: str) -> None:
    result = ListenerAgent._fast_classify(text)
    assert result is None or result.intent != "get_resources"


def test_system_info_intent_no_longer_advertises_cpu_or_ram() -> None:
    from voxos.agents.listener.intent import KNOWN_INTENTS

    examples = " ".join(KNOWN_INTENTS["get_system_info"].example_phrases).lower()
    assert "cpu" not in examples and "ram" not in examples
    assert "battery" in KNOWN_INTENTS["get_resources"].description.lower()


DATA = {
    "cpu": {"percent": 11.0},
    "memory": {"percent": 90.0, "used_gb": 14.13, "total_gb": 15.7},
    "disk": {"percent": 85.0},
    "battery": {"percent": 76.4, "power_plugged": True},
}


def _say(request: str, data=DATA) -> str:
    text, _ = ConversationService._sentence_for(data, "get_resources", request)
    return text


def test_battery_question_gets_only_the_battery() -> None:
    assert _say("what's my battery percentage") == "Battery is at 76% and charging."


def test_unplugged_battery_says_so() -> None:
    data = {**DATA, "battery": {"percent": 40, "power_plugged": False}}
    assert _say("battery?", data) == "Battery is at 40% on battery power."


def test_desktop_without_battery_is_honest() -> None:
    data = {**DATA, "battery": {"percent": None, "power_plugged": None}}
    assert _say("battery level", data) == "I can't read a battery on this computer."


def test_cpu_question_gets_only_the_cpu() -> None:
    assert _say("how's my cpu doing?") == "CPU is at 11.0%."


def test_general_request_reads_everything() -> None:
    assert _say("system stats") == (
        "Battery is at 76% and charging, CPU is at 11.0%, "
        "memory at 90.0% (14.13 of 15.7 GB used) and disk at 85.0%."
    )
