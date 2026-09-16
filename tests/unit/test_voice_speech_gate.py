"""Tests for the speech-quality gate that keeps noise out of the agent pipeline."""

from __future__ import annotations

import pytest


@pytest.fixture()
def gate():
    from voxos.voice.filters import SpeechQualityGate

    return SpeechQualityGate()


def test_accepts_a_normal_command(gate) -> None:
    assert gate.reject_reason("open chrome and search for pizza", 0.95) is None


def test_rejects_empty_transcript(gate) -> None:
    assert gate.reject_reason("   ", 0.99) == "empty"


def test_rejects_punctuation_only_transcript(gate) -> None:
    assert gate.reject_reason("...", 0.99) == "no_speech"


def test_rejects_low_confidence_transcript(gate) -> None:
    assert gate.reject_reason("open chrome", 0.05) == "low_confidence"


def test_rejects_single_filler_word(gate) -> None:
    assert gate.reject_reason("umm", 0.99) == "filler"


def test_rejects_whisper_style_thank_you_hallucination(gate) -> None:
    assert gate.reject_reason("Thank you.", 0.99) == "filler"


def test_rejects_non_speech_marker(gate) -> None:
    assert gate.reject_reason("[Music]", 0.99) == "non_speech"


def test_allows_short_control_command(gate) -> None:
    assert gate.reject_reason("stop", 0.9) is None


def test_allows_wake_phrase_alone(gate) -> None:
    assert gate.reject_reason("hey voxos", 0.9) is None


def test_rejects_echo_of_what_the_assistant_just_said(gate) -> None:
    gate.note_bot_utterance("I opened Chrome for you.")

    assert gate.reject_reason("I opened Chrome for you", 0.99) == "self_echo"


def test_accept_is_the_inverse_of_reject_reason(gate) -> None:
    assert gate.accept("open chrome", 0.9) is True
    assert gate.accept("umm", 0.9) is False


def test_confidence_floor_is_configurable() -> None:
    from voxos.voice.filters import SpeechQualityGate

    strict = SpeechQualityGate(min_confidence=0.8)

    assert strict.reject_reason("open chrome", 0.5) == "low_confidence"
