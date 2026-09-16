"""Tests for classifying a hotkey press as a tap or a hold.

Tap toggles a hands-free session; hold is push-to-talk for one utterance.
Keeping the rule a pure function of press/release timestamps means it can be
tested without a global keyboard hook.
"""

from __future__ import annotations

import pytest

from voxos.ui.hotkey import HotkeyGesture, classify_gesture


def test_a_quick_press_is_a_tap() -> None:
    assert classify_gesture(held_secs=0.1, hold_threshold=0.35) is HotkeyGesture.TAP


def test_a_long_press_is_a_hold() -> None:
    assert classify_gesture(held_secs=1.2, hold_threshold=0.35) is HotkeyGesture.HOLD


def test_the_threshold_itself_counts_as_a_hold() -> None:
    assert classify_gesture(held_secs=0.35, hold_threshold=0.35) is HotkeyGesture.HOLD


def test_just_under_the_threshold_is_a_tap() -> None:
    assert classify_gesture(held_secs=0.349, hold_threshold=0.35) is HotkeyGesture.TAP


def test_an_instantaneous_press_is_a_tap() -> None:
    assert classify_gesture(held_secs=0.0, hold_threshold=0.35) is HotkeyGesture.TAP


@pytest.mark.parametrize("held", [-0.0, -1.0])
def test_a_negative_duration_is_treated_as_a_tap(held: float) -> None:
    assert classify_gesture(held_secs=held, hold_threshold=0.35) is HotkeyGesture.TAP
