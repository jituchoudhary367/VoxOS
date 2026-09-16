"""
Deepgram STT Provider (batch / pre-recorded audio).

Real-time voice goes through Pipecat's streaming Deepgram service in
`voxos.voice.pipeline`. This provider covers the non-streaming cases — a
recorded clip, a voice note, a test fixture — behind the `STTProvider`
interface so the batch transcriber can be swapped without touching callers.

Written against deepgram-sdk v4+, whose client is a resource tree
(`client.listen.v1.media.transcribe_file`). The v3 helpers this module used to
import (`PrerecordedOptions`, `LiveOptions`, `deepgram.audio.microphone`) no
longer exist.
"""

from __future__ import annotations

import io
import wave
from typing import Any

from deepgram import AsyncDeepgramClient

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.voice.stt.base import STTProvider, TranscriptionResult

log = get_logger(__name__)
metrics = get_metrics()


def pcm_to_wav(audio_bytes: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw 16-bit PCM in a WAV container.

    The batch endpoint infers the audio format from the payload and the SDK
    exposes no sample-rate parameter, so raw PCM has to carry its own header or
    Deepgram guesses the rate and returns garbage.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio_bytes)
    return buffer.getvalue()


def parse_listen_response(response: Any) -> TranscriptionResult:
    """Turn a Deepgram listen response into a `TranscriptionResult`.

    Kept separate from the network call so the response shape — which has
    changed across SDK majors — is covered by tests.
    """
    results = getattr(response, "results", None)
    channels = getattr(results, "channels", None) or []
    if not channels:
        return TranscriptionResult(text="", language="en", confidence=0.0)

    channel = channels[0]
    alternatives = getattr(channel, "alternatives", None) or []
    if not alternatives:
        return TranscriptionResult(text="", language="en", confidence=0.0)

    best = alternatives[0]
    return TranscriptionResult(
        text=getattr(best, "transcript", "") or "",
        language=getattr(channel, "detected_language", None) or "en",
        confidence=float(getattr(best, "confidence", None) or 0.0),
        is_final=True,
    )


class DeepgramSTTProvider(STTProvider):
    """Batch Deepgram transcription with language detection."""

    def __init__(self) -> None:
        settings = get_settings().stt
        self._client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
        self._model = settings.deepgram_model
        self._language = settings.deepgram_language

    @property
    def name(self) -> str:
        return "deepgram"

    async def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000
    ) -> TranscriptionResult:
        """Transcribe raw 16-bit PCM audio."""
        # "multi" is a streaming-only language code; batch requests use
        # language detection instead.
        language = None if self._language == "multi" else self._language

        async with metrics.async_timer("stt.latency", provider=self.name):
            try:
                response = await self._client.listen.v1.media.transcribe_file(
                    request=pcm_to_wav(audio_bytes, sample_rate),
                    model=self._model,
                    language=language,
                    detect_language=language is None,
                    smart_format=True,
                    punctuate=True,
                    diarize=False,
                )
            except Exception as e:
                log.error("stt_transcription_error", provider=self.name, error=str(e))
                metrics.increment("stt.errors", provider=self.name)
                return TranscriptionResult(text="", language="en", confidence=0.0)

        result = parse_listen_response(response)
        metrics.increment("stt.requests", provider=self.name)
        log.debug(
            "stt_transcribed",
            provider=self.name,
            length=len(result.text),
            language=result.language,
            confidence=round(result.confidence, 3),
        )
        return result

    async def health_check(self) -> bool:
        """Check Deepgram API availability."""
        try:
            await self._client.manage.v1.models.list()
            return True
        except Exception as e:
            log.warning("stt_health_check_failed", provider=self.name, error=str(e))
            return False


class MockSTTProvider(STTProvider):
    """
    Mock STT provider for testing without Deepgram API key.
    Returns a fixed transcription or echoes passed text.
    """

    @property
    def name(self) -> str:
        return "mock"

    async def transcribe(
        self, audio_bytes: bytes, sample_rate: int = 16000
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text="What time is it?",
            language="en",
            confidence=1.0,
        )

    async def health_check(self) -> bool:
        return True
