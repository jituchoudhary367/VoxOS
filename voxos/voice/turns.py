"""
Turn assembly — STT fragments in, one vetted user instruction out.

Streaming STT emits a finalized transcript per phrase, so a single spoken
sentence usually arrives as several fragments between the turn's start and
stop. This module buffers them, tracks the weakest confidence seen, and applies
the speech-quality gate once, on the complete utterance — judging the whole
sentence rather than each fragment in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from voxos.voice.filters import SpeechQualityGate


@dataclass(frozen=True)
class AssembledTurn:
    """The outcome of one user turn.

    Attributes:
        text: The instruction to run, or None when there is nothing to run.
        confidence: Lowest STT confidence among the fragments in the turn.
        rejected_because: Why the turn was discarded, or None when the turn was
            accepted or the user simply said nothing.
    """

    text: str | None
    confidence: float
    rejected_because: str | None


class TurnAssembler:
    """Collects transcription fragments and vets the completed utterance."""

    def __init__(self, quality_gate: SpeechQualityGate | None = None) -> None:
        self.quality_gate = quality_gate or SpeechQualityGate()
        self._fragments: list[str] = []
        self._confidence = 1.0

    def start(self) -> None:
        """Begin a new turn, discarding anything buffered from the last one."""
        self._fragments = []
        self._confidence = 1.0

    def add_fragment(self, text: str, confidence: float = 1.0) -> None:
        """Append a finalized STT fragment to the current turn."""
        stripped = (text or "").strip()
        if not stripped:
            return
        self._fragments.append(stripped)
        self._confidence = min(self._confidence, confidence)

    def finish(self) -> AssembledTurn:
        """Close the turn and decide whether it should reach the agents."""
        full_text = " ".join(self._fragments).strip()
        confidence = self._confidence
        self.start()

        if not full_text:
            return AssembledTurn(text=None, confidence=confidence, rejected_because=None)

        reason = self.quality_gate.reject_reason(full_text, confidence)
        if reason is not None:
            return AssembledTurn(text=None, confidence=confidence, rejected_because=reason)

        return AssembledTurn(text=full_text, confidence=confidence, rejected_because=None)
