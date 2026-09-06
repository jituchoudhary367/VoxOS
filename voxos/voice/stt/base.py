"""
STT Provider Abstract Base Class.

All Speech-to-Text providers must implement this interface.
This allows swapping Deepgram → Whisper → any other STT
without changing the voice pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    """Result from a speech-to-text transcription."""

    text: str
    language: str = "en"
    confidence: float = 1.0
    is_final: bool = True
    words: list[dict] | None = None  # Word-level timing if available


class STTProvider(ABC):
    """Abstract STT provider interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> TranscriptionResult:
        """
        Transcribe audio bytes to text.
        
        Args:
            audio_bytes: Raw PCM audio data
            sample_rate: Sample rate in Hz
            
        Returns:
            TranscriptionResult with text and metadata
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable."""
