"""Tests for the mic gate that decides when captured audio can be trusted.

Two independent reasons close it:
  - Vox is speaking (echo suppression), plus a hangover after playback ends
  - push-to-talk is not held (manual mute)
"""

from __future__ import annotations

import pytest


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def gate(clock: FakeClock):
    from voxos.voice.gate import MicGate

    return MicGate(hangover_secs=0.5, clock=clock)


class TestEchoSuppression:
    def test_microphone_is_open_by_default(self, gate) -> None:
        assert gate.should_drop_audio() is False

    def test_microphone_is_muted_while_vox_speaks(self, gate) -> None:
        gate.bot_started()

        assert gate.should_drop_audio() is True

    def test_microphone_stays_muted_during_the_hangover_window(self, gate, clock) -> None:
        gate.bot_started()
        gate.bot_stopped()
        clock.advance(0.3)

        assert gate.should_drop_audio() is True

    def test_microphone_reopens_after_the_hangover_window(self, gate, clock) -> None:
        gate.bot_started()
        gate.bot_stopped()
        clock.advance(0.6)

        assert gate.should_drop_audio() is False

    def test_second_utterance_remutes_after_reopening(self, gate, clock) -> None:
        gate.bot_started()
        gate.bot_stopped()
        clock.advance(0.6)
        gate.bot_started()

        assert gate.should_drop_audio() is True

    def test_zero_hangover_reopens_immediately(self, clock) -> None:
        from voxos.voice.gate import MicGate

        gate = MicGate(hangover_secs=0.0, clock=clock)
        gate.bot_started()
        gate.bot_stopped()

        assert gate.should_drop_audio() is False


class TestPushToTalk:
    def test_manual_mute_closes_the_microphone(self, gate) -> None:
        gate.set_manual_mute(True)

        assert gate.should_drop_audio() is True

    def test_releasing_the_manual_mute_reopens_the_microphone(self, gate) -> None:
        gate.set_manual_mute(True)
        gate.set_manual_mute(False)

        assert gate.should_drop_audio() is False

    def test_manual_mute_outlasts_vox_finishing_its_reply(self, gate, clock) -> None:
        gate.set_manual_mute(True)
        gate.bot_started()
        gate.bot_stopped()
        clock.advance(10.0)

        assert gate.should_drop_audio() is True

    def test_vox_speaking_mutes_even_when_push_to_talk_is_held(self, gate) -> None:
        gate.set_manual_mute(False)
        gate.bot_started()

        assert gate.should_drop_audio() is True

    def test_releasing_push_to_talk_during_playback_keeps_it_muted(self, gate) -> None:
        gate.bot_started()
        gate.set_manual_mute(False)

        assert gate.should_drop_audio() is True

    def test_gate_can_start_muted(self, clock) -> None:
        from voxos.voice.gate import MicGate

        gate = MicGate(hangover_secs=0.5, clock=clock, manually_muted=True)

        assert gate.should_drop_audio() is True
