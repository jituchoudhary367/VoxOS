"""Tests for assembling STT fragments into a single accepted user turn."""

from __future__ import annotations

import pytest

from voxos.voice.filters import SpeechQualityGate


@pytest.fixture()
def assembler():
    from voxos.voice.turns import TurnAssembler

    return TurnAssembler(quality_gate=SpeechQualityGate())


def test_joins_fragments_into_one_utterance(assembler) -> None:
    assembler.start()
    assembler.add_fragment("open chrome", 0.98)
    assembler.add_fragment("and search for pizza", 0.97)

    assert assembler.finish().text == "open chrome and search for pizza"


def test_finished_turn_carries_the_lowest_fragment_confidence(assembler) -> None:
    assembler.start()
    assembler.add_fragment("open chrome", 0.98)
    assembler.add_fragment("and search for pizza", 0.61)

    assert assembler.finish().confidence == pytest.approx(0.61)


def test_noise_transcript_produces_no_turn(assembler) -> None:
    assembler.start()
    assembler.add_fragment("Thank you.", 0.99)

    turn = assembler.finish()

    assert turn.text is None
    assert turn.rejected_because == "filler"


def test_low_confidence_transcript_produces_no_turn(assembler) -> None:
    assembler.start()
    assembler.add_fragment("open chrome", 0.02)

    assert assembler.finish().rejected_because == "low_confidence"


def test_silence_produces_no_turn_and_no_rejection_reason(assembler) -> None:
    assembler.start()

    turn = assembler.finish()

    assert turn.text is None
    assert turn.rejected_because is None


def test_a_new_turn_discards_the_previous_partial_buffer(assembler) -> None:
    assembler.start()
    assembler.add_fragment("open chrome", 0.9)
    assembler.start()
    assembler.add_fragment("close notepad", 0.9)

    assert assembler.finish().text == "close notepad"


def test_finishing_twice_does_not_replay_the_turn(assembler) -> None:
    assembler.start()
    assembler.add_fragment("open chrome", 0.9)
    assembler.finish()

    assert assembler.finish().text is None


def test_blank_fragments_are_ignored(assembler) -> None:
    assembler.start()
    assembler.add_fragment("   ", 0.1)
    assembler.add_fragment("open chrome", 0.9)

    assert assembler.finish().text == "open chrome"


def test_assistant_echo_is_rejected(assembler) -> None:
    assembler.quality_gate.note_bot_utterance("Chrome is open now")
    assembler.start()
    assembler.add_fragment("Chrome is open now", 0.99)

    assert assembler.finish().rejected_because == "self_echo"
