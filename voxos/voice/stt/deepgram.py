"""
Deepgram STT Provider.

Uses the Deepgram nova-3 model for multilingual transcription.
Supports streaming audio for real-time voice interaction.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from deepgram import DeepgramClient, PrerecordedOptions, LiveOptions
from deepgram.audio.microphone import Microphone

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.voice.stt.base import STTProvider, TranscriptionResult

log = get_logger(__name__)
metrics = get_metrics()


class DeepgramSTTProvider(STTProvider):
    """
    Deepgram STT provider supporting:
    - Pre-recorded audio transcription
    - Streaming/live transcription
    - Multilingual input (nova-3 model)
    - Language detection
    """

    def __init__(self) -> None:
        settings = get_settings().stt
        self._client = DeepgramClient(api_key=settings.deepgram_api_key)
        self._model = settings.deepgram_model
        self._language = settings.deepgram_language

    @property
    def name(self) -> str:
        return "deepgram"

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        """Transcribe pre-recorded audio bytes."""
        options = PrerecordedOptions(
            model=self._model,
            language=self._language if self._language != "multi" else None,
            detect_language=True,
            smart_format=True,
            punctuate=True,
            diarize=False,
        )

        async with metrics.async_timer("stt.latency", provider=self.name):
            try:
                response = await self._client.listen.asyncprerecorded.v("1").transcribe_file(
                    {"buffer": audio_bytes, "mimetype": f"audio/raw;rate={sample_rate};encoding=linear16"},
                    options,
                )

                result = response.results
                if not result or not result.channels:
                    return TranscriptionResult(text="", language="en", confidence=0.0)

                channel = result.channels[0].alternatives[0]
                detected_lang = (
                    result.channels[0].detected_language or "en"
                ) if hasattr(result.channels[0], "detected_language") else "en"

                text = channel.transcript or ""
                confidence = channel.confidence or 1.0

                metrics.increment("stt.requests", provider=self.name)
                log.debug(
                    "stt_transcribed",
                    provider=self.name,
                    length=len(text),
                    language=detected_lang,
                    confidence=round(confidence, 3),
                )

                return TranscriptionResult(
                    text=text,
                    language=detected_lang,
                    confidence=float(confidence),
                    is_final=True,
                )

            except Exception as e:
                log.error("stt_transcription_error", provider=self.name, error=str(e))
                metrics.increment("stt.errors", provider=self.name)
                return TranscriptionResult(text="", language="en", confidence=0.0)

    async def health_check(self) -> bool:
        """Check Deepgram API availability."""
        try:
            # Minimal check — verify client can connect
            models = await self._client.manage.asyncv1.get_all_models()
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

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        return TranscriptionResult(
            text="What time is it?",
            language="en",
            confidence=1.0,
        )

    async def health_check(self) -> bool:
        return True
