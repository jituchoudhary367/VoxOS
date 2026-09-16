"""Tests for the Deepgram STT settings built from VoxOS configuration."""

from __future__ import annotations

import pytest

from voxos.config import STTSettings


def _settings(**overrides) -> STTSettings:
    base = {
        "deepgram_api_key": "test-key",
        "deepgram_model": "nova-3",
        "deepgram_language": "multi",
    }
    base.update(overrides)
    return STTSettings(**base)


def test_model_and_language_come_from_configuration() -> None:
    from voxos.voice.pipeline import build_stt_settings

    built = build_stt_settings(_settings(deepgram_model="nova-2", deepgram_language="en"))

    assert built.model == "nova-2"
    assert built.language == "en"


def test_interim_results_stay_on_for_low_latency_turn_detection() -> None:
    from voxos.voice.pipeline import build_stt_settings

    assert build_stt_settings(_settings()).interim_results is True


def test_keyterms_are_passed_through_when_configured() -> None:
    from voxos.voice.pipeline import build_stt_settings

    built = build_stt_settings(_settings(deepgram_keyterms="VoxOS,Exotel,Hinglish"))

    assert built.keyterm == ["VoxOS", "Exotel", "Hinglish"]


def test_blank_keyterm_entries_are_dropped() -> None:
    from voxos.voice.pipeline import build_stt_settings

    built = build_stt_settings(_settings(deepgram_keyterms="VoxOS, ,  Exotel "))

    assert built.keyterm == ["VoxOS", "Exotel"]


def test_no_keyterms_configured_leaves_the_option_unset() -> None:
    from pipecat.utils.types import NOT_GIVEN

    from voxos.voice.pipeline import build_stt_settings

    assert build_stt_settings(_settings()).keyterm is NOT_GIVEN


def test_keyterms_are_only_sent_to_models_that_support_them() -> None:
    from pipecat.utils.types import NOT_GIVEN

    from voxos.voice.pipeline import build_stt_settings

    built = build_stt_settings(
        _settings(deepgram_model="nova-2", deepgram_keyterms="VoxOS")
    )

    assert built.keyterm is NOT_GIVEN


@pytest.mark.parametrize("model", ["nova-3", "nova-3-general", "nova-3-medical"])
def test_every_nova_3_variant_accepts_keyterms(model: str) -> None:
    from voxos.voice.pipeline import build_stt_settings

    built = build_stt_settings(_settings(deepgram_model=model, deepgram_keyterms="VoxOS"))

    assert built.keyterm == ["VoxOS"]
