"""Tests for picking an edge-tts voice.

edge-tts is now the fallback path for languages Deepgram Aura cannot speak, so
a configured English override must not hijack a Hindi reply.
"""

from __future__ import annotations

import pytest

from voxos.voice.tts.edge_tts_provider import EdgeTTSProvider


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> EdgeTTSProvider:
    monkeypatch.setenv("EDGE_TTS_VOICE", "")
    from voxos.config import get_settings

    get_settings.cache_clear()
    return EdgeTTSProvider()


@pytest.fixture()
def provider_with_override(monkeypatch: pytest.MonkeyPatch) -> EdgeTTSProvider:
    monkeypatch.setenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    from voxos.config import get_settings

    get_settings.cache_clear()
    return EdgeTTSProvider()


def test_hindi_gets_a_hindi_voice(provider) -> None:
    assert provider._select_voice("hi") == "hi-IN-SwaraNeural"


def test_hinglish_gets_a_hindi_voice(provider) -> None:
    assert provider._select_voice("hi-Latn") == "hi-IN-SwaraNeural"


def test_english_gets_the_default_voice(provider) -> None:
    assert provider._select_voice("en") == "en-IN-NeerjaNeural"


def test_override_applies_to_english(provider_with_override) -> None:
    assert provider_with_override._select_voice("en") == "en-US-AriaNeural"


def test_english_override_does_not_hijack_hindi(provider_with_override) -> None:
    assert provider_with_override._select_voice("hi") == "hi-IN-SwaraNeural"


def test_english_override_does_not_hijack_tamil(provider_with_override) -> None:
    assert provider_with_override._select_voice("ta") == "ta-IN-PallaviNeural"


def test_unknown_language_falls_back_to_the_default(provider) -> None:
    assert provider._select_voice("xx") == "en-IN-NeerjaNeural"
