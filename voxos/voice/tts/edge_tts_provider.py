"""
TTS Provider — Text to Speech using edge-tts.

edge-tts is completely free, offline-capable, and supports
250+ voices across 40+ languages. No API key required.
"""

from __future__ import annotations

import asyncio
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

import edge_tts

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


@dataclass
class TTSResult:
    """Result from text-to-speech synthesis."""

    audio_bytes: bytes
    sample_rate: int = 24000
    format: str = "mp3"
    duration_ms: float = 0.0


class TTSProvider(ABC):
    """Abstract TTS provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def synthesize(self, text: str, language: str = "en") -> TTSResult:
        """Convert text to speech audio bytes."""


class EdgeTTSProvider(TTSProvider):
    """
    Microsoft Edge TTS provider via edge-tts library.
    
    Free, no API key required, works offline after first run.
    250+ voices, 40+ languages.
    
    Voice selection:
        en     → en-IN-NeerjaNeural (Indian English — fits VoxOS persona)
        hi     → hi-IN-SwaraNeural
        auto   → defaults to English
    """

    # Voice mapping by language code
    VOICE_MAP: dict[str, str] = {
        "en": "en-IN-NeerjaNeural",
        "en-US": "en-US-JennyNeural",
        "en-GB": "en-GB-SoniaNeural",
        "hi": "hi-IN-SwaraNeural",
        "ta": "ta-IN-PallaviNeural",
        "te": "te-IN-ShrutiNeural",
        "mr": "mr-IN-AarohiNeural",
        "bn": "bn-IN-TanishaaNeural",
        "kn": "kn-IN-SapnaNeural",
        "gu": "gu-IN-DhwaniNeural",
        "pa": "pa-IN-OjasNeural",
        "fr": "fr-FR-DeniseNeural",
        "de": "de-DE-KatjaNeural",
        "es": "es-ES-ElviraNeural",
        "ar": "ar-AE-FatimaNeural",
        "ja": "ja-JP-NanamiNeural",
        "ko": "ko-KR-SunHiNeural",
        "zh": "zh-CN-XiaoxiaoNeural",
        "default": "en-IN-NeerjaNeural",
    }

    def __init__(self) -> None:
        settings = get_settings().tts
        self._rate = f"{settings.edge_tts_rate:+d}%"  # e.g., "+10%"
        self._volume = f"{settings.edge_tts_volume:+d}%"
        self._custom_voice = settings.edge_tts_voice

    @property
    def name(self) -> str:
        return "edge-tts"

    async def synthesize(self, text: str, language: str = "en") -> TTSResult:
        """Synthesize text to MP3 audio bytes."""
        if not text or not text.strip():
            return TTSResult(audio_bytes=b"", format="mp3")

        voice = self._select_voice(language)

        async with metrics.async_timer("tts.latency", provider=self.name):
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=self._rate,
                    volume=self._volume,
                )

                audio_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])

                audio_bytes = b"".join(audio_chunks)

                metrics.increment("tts.requests", provider=self.name, language=language)
                log.debug(
                    "tts_synthesized",
                    provider=self.name,
                    voice=voice,
                    text_length=len(text),
                    audio_bytes=len(audio_bytes),
                )

                return TTSResult(audio_bytes=audio_bytes, format="mp3")

            except Exception as e:
                log.error("tts_error", provider=self.name, error=str(e), voice=voice)
                metrics.increment("tts.errors", provider=self.name)
                return TTSResult(audio_bytes=b"", format="mp3")

    def _select_voice(self, language: str) -> str:
        """Select the best voice for a given language code."""
        if self._custom_voice:
            return self._custom_voice
        # Try exact match, then language prefix, then default
        return (
            self.VOICE_MAP.get(language)
            or self.VOICE_MAP.get(language.split("-")[0])
            or self.VOICE_MAP["default"]
        )

    @classmethod
    async def list_voices(cls) -> list[dict]:
        """List all available edge-tts voices."""
        voices = await edge_tts.list_voices()
        return voices


@lru_cache(maxsize=1)
def get_tts_provider() -> TTSProvider:
    """Return cached TTS provider singleton."""
    provider = EdgeTTSProvider()
    log.info("tts_provider_initialized", provider=provider.name)
    return provider
