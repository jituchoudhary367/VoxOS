"""Tests for the batch Deepgram STT provider.

This provider is the swap point for non-streaming transcription. It broke
silently when deepgram-sdk moved to v4+: `PrerecordedOptions` and
`deepgram.audio.microphone` no longer exist, so importing the module raised
ImportError.
"""

from __future__ import annotations

import pytest

from voxos.voice.stt.deepgram import parse_listen_response


class FakeAlternative:
    def __init__(self, transcript: str, confidence: float) -> None:
        self.transcript = transcript
        self.confidence = confidence


class FakeChannel:
    def __init__(self, alternatives, detected_language=None) -> None:
        self.alternatives = alternatives
        self.detected_language = detected_language


class FakeResults:
    def __init__(self, channels) -> None:
        self.channels = channels


class FakeResponse:
    def __init__(self, results) -> None:
        self.results = results


def _response(transcript: str, confidence: float = 0.9, language: str | None = None):
    return FakeResponse(
        FakeResults([FakeChannel([FakeAlternative(transcript, confidence)], language)])
    )


def test_reads_the_transcript_from_the_first_alternative() -> None:
    assert parse_listen_response(_response("open chrome")).text == "open chrome"


def test_reads_the_confidence_from_the_first_alternative() -> None:
    result = parse_listen_response(_response("open chrome", confidence=0.42))

    assert result.confidence == pytest.approx(0.42)


def test_reports_the_detected_language() -> None:
    result = parse_listen_response(_response("chrome kholo", language="hi"))

    assert result.language == "hi"


def test_defaults_to_english_when_no_language_was_detected() -> None:
    assert parse_listen_response(_response("open chrome")).language == "en"


def test_an_empty_response_yields_no_text_and_zero_confidence() -> None:
    result = parse_listen_response(FakeResponse(FakeResults([])))

    assert result.text == ""
    assert result.confidence == 0.0


def test_a_response_without_results_yields_no_text() -> None:
    assert parse_listen_response(FakeResponse(None)).text == ""


def test_a_channel_without_alternatives_yields_no_text() -> None:
    result = parse_listen_response(FakeResponse(FakeResults([FakeChannel([])])))

    assert result.text == ""


def test_results_are_marked_final() -> None:
    assert parse_listen_response(_response("open chrome")).is_final is True


class TestWavWrapping:
    """Deepgram's batch endpoint sniffs the container, and the SDK exposes no
    sample-rate parameter — so raw PCM has to arrive as a WAV file."""

    def test_wrapped_audio_is_a_riff_wave_file(self) -> None:
        from voxos.voice.stt.deepgram import pcm_to_wav

        assert pcm_to_wav(b"\x00\x01" * 100, 16000)[:4] == b"RIFF"
        assert pcm_to_wav(b"\x00\x01" * 100, 16000)[8:12] == b"WAVE"

    def test_wrapped_audio_records_the_sample_rate(self) -> None:
        import io
        import wave

        from voxos.voice.stt.deepgram import pcm_to_wav

        with wave.open(io.BytesIO(pcm_to_wav(b"\x00\x01" * 100, 24000))) as wav:
            assert wav.getframerate() == 24000

    def test_wrapped_audio_is_mono_16_bit(self) -> None:
        import io
        import wave

        from voxos.voice.stt.deepgram import pcm_to_wav

        with wave.open(io.BytesIO(pcm_to_wav(b"\x00\x01" * 100, 16000))) as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2

    def test_wrapped_audio_preserves_every_sample(self) -> None:
        import io
        import wave

        from voxos.voice.stt.deepgram import pcm_to_wav

        pcm = b"\x00\x01" * 100
        with wave.open(io.BytesIO(pcm_to_wav(pcm, 16000))) as wav:
            assert wav.readframes(wav.getnframes()) == pcm
