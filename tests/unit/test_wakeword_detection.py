"""Tests for wake word trigger selection.

The historical defect: the engine loaded every openwakeword pretrained model
and fired on any of them crossing the threshold — including the "timer" and
"weather" intent models, which trigger on ordinary conversation.
"""

from __future__ import annotations

import pytest

from voxos.voice.wakeword.detection import select_trigger


def test_triggers_on_a_configured_model_above_threshold() -> None:
    scores = {"hey_jarvis_v0.1": 0.91}

    assert select_trigger(scores, {"hey_jarvis_v0.1"}, 0.6) == "hey_jarvis_v0.1"


def test_ignores_a_configured_model_below_threshold() -> None:
    scores = {"hey_jarvis_v0.1": 0.4}

    assert select_trigger(scores, {"hey_jarvis_v0.1"}, 0.6) is None


def test_ignores_models_that_were_not_configured() -> None:
    scores = {"timer": 0.99, "weather": 0.97}

    assert select_trigger(scores, {"hey_jarvis_v0.1"}, 0.6) is None


def test_picks_the_strongest_match_when_several_models_fire() -> None:
    scores = {"hey_jarvis_v0.1": 0.72, "alexa_v0.1": 0.95}

    assert select_trigger(scores, {"hey_jarvis_v0.1", "alexa_v0.1"}, 0.6) == "alexa_v0.1"


def test_an_empty_allowlist_accepts_any_model() -> None:
    scores = {"hey_jarvis_v0.1": 0.8}

    assert select_trigger(scores, set(), 0.6) == "hey_jarvis_v0.1"


def test_threshold_is_inclusive() -> None:
    scores = {"hey_jarvis_v0.1": 0.6}

    assert select_trigger(scores, {"hey_jarvis_v0.1"}, 0.6) == "hey_jarvis_v0.1"


def test_no_scores_means_no_trigger() -> None:
    assert select_trigger({}, {"hey_jarvis_v0.1"}, 0.6) is None


@pytest.mark.parametrize("score", [float("nan"), None, "loud"])
def test_unusable_scores_are_ignored(score) -> None:
    assert select_trigger({"hey_jarvis_v0.1": score}, {"hey_jarvis_v0.1"}, 0.6) is None
