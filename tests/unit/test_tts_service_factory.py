"""Tests for building the in-pipeline Deepgram TTS service from configuration."""

from __future__ import annotations

from pipecat.services.deepgram.tts import DeepgramTTSService

from voxos.config import STTSettings, TTSSettings, VoiceSettings
from voxos.voice.pipeline import build_tts_service


def _stt(key: str = "test-key") -> STTSettings:
    return STTSettings(deepgram_api_key=key)


def _tts(**overrides) -> TTSSettings:
    base = {"tts_provider": "deepgram"}
    base.update(overrides)
    return TTSSettings(**base)


def _voice() -> VoiceSettings:
    return VoiceSettings()


def test_deepgram_provider_builds_a_streaming_service() -> None:
    service = build_tts_service(_tts(), _stt(), _voice())

    assert isinstance(service, DeepgramTTSService)


def test_edge_provider_builds_no_streaming_service() -> None:
    assert build_tts_service(_tts(tts_provider="edge"), _stt(), _voice()) is None


def test_missing_api_key_builds_no_streaming_service() -> None:
    assert build_tts_service(_tts(), _stt(key=""), _voice()) is None


def test_configured_voice_is_used() -> None:
    service = build_tts_service(
        _tts(deepgram_tts_voice="aura-2-asteria-en"), _stt(), _voice()
    )

    # The service only exposes its voice through the settings it was given;
    # `sample_rate` likewise stays 0 until the pipeline sends a StartFrame.
    assert service._settings.voice == "aura-2-asteria-en"


def test_service_output_rate_matches_the_configured_playback_rate() -> None:
    voice = VoiceSettings(tts_sample_rate=24000)

    service = build_tts_service(_tts(), _stt(), voice)

    assert service._init_sample_rate == 24000


def test_service_output_rate_follows_a_changed_playback_rate() -> None:
    service = build_tts_service(_tts(), _stt(), VoiceSettings(tts_sample_rate=16000))

    assert service._init_sample_rate == 16000


def test_provider_name_is_case_insensitive() -> None:
    assert build_tts_service(_tts(tts_provider="DeepGram"), _stt(), _voice()) is not None
