"""Tests for loading only the intended openwakeword models.

openwakeword names a model's prediction key after however it was requested
("hey_jarvis" vs "hey_jarvis_v0.1"), so an allowlist written by hand silently
stops matching. The loader returns the keys the model will actually emit.
"""

from __future__ import annotations

import pytest

openwakeword = pytest.importorskip("openwakeword")

from voxos.voice.wakeword.detection import load_wakeword_model  # noqa: E402


def test_loads_only_the_requested_wake_word() -> None:
    _, keys = load_wakeword_model(["hey_jarvis"])

    assert keys == frozenset({"hey_jarvis"})


def test_trigger_keys_match_however_the_model_was_named() -> None:
    _, keys = load_wakeword_model(["hey_jarvis_v0.1"])

    assert keys == frozenset({"hey_jarvis_v0.1"})


def test_intent_models_are_not_loaded_alongside_the_wake_word() -> None:
    _, keys = load_wakeword_model(["hey_jarvis"])

    assert "timer" not in keys
    assert "weather" not in keys


def test_the_loaded_model_scores_only_the_requested_wake_word() -> None:
    import numpy as np

    model, keys = load_wakeword_model(["hey_jarvis"])
    prediction = model.predict(np.zeros(1280, dtype=np.int16))

    assert set(prediction) == set(keys)


def test_an_unknown_model_name_raises_rather_than_loading_everything() -> None:
    with pytest.raises(ValueError, match="Could not find pretrained model"):
        load_wakeword_model(["definitely_not_a_wakeword_model"])
