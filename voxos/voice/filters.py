"""
Speech-quality gate.

Streaming STT will happily transcribe a cough, a fan, a TV in the background,
or VoxOS' own voice leaking back through the speakers. Those transcripts look
like real commands to the agent pipeline, which is where "the assistant
hallucinated a task nobody asked for" comes from.

This module is the last line of defence *after* VAD: it drops transcripts that
carry no instruction. It is deliberately pure (no I/O, no Pipecat types) so the
rules can be unit-tested and tuned without a microphone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Fully-bracketed transcripts are non-speech annotations: "[Music]", "(applause)".
_NON_SPEECH_MARKER = re.compile(r"^[\[\(].*[\]\)]$")

# Single hesitation sounds, stretched to any length: "um", "uhhh", "hmmm".
_HESITATION = re.compile(r"^(u+m+|u+h+|h+m+|m+h*|a+h+|o+h+|e+r+)$")

# Whole utterances that carry no instruction. Whisper and Deepgram both emit
# these when handed silence, music, or room noise.
_FILLER_PHRASES = frozenset(
    {
        "thank you",
        "thanks",
        "thank you very much",
        "thanks for watching",
        "bye",
        "goodbye",
        "you",
        "the",
        "and",
        "i",
        "a",
        "so",
        "yeah",
        "yes",
        "no",
        "ok",
        "okay",
        "oh",
        "ah",
        "hmm",
        "mmm",
        "la la la",
        "subscribe",
        "please subscribe",
    }
)

# Short words that ARE real commands and must survive the min-word rule.
_SHORT_COMMANDS = frozenset(
    {
        "stop",
        "pause",
        "resume",
        "play",
        "next",
        "previous",
        "back",
        "cancel",
        "mute",
        "unmute",
        "close",
        "quit",
        "exit",
        "undo",
        "redo",
        "louder",
        "quieter",
        "repeat",
        "help",
        "screenshot",
    }
)

_WAKE_PREFIXES = frozenset({"hey", "hi", "hello", "ok", "okay", "yo"})
# The agent is Vox. The variants are how Deepgram commonly renders the name.
_WAKE_NAMES = frozenset({"vox", "voxos", "vaux", "voks", "wox", "voxus"})

_WORD_RE = re.compile(r"[a-z0-9ऀ-ॿ]+")


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens, keeping Devanagari so Hindi input is not dropped."""
    return _WORD_RE.findall(text.lower())


@dataclass
class SpeechQualityGate:
    """Decides whether a finalized transcript is worth sending to the agents.

    Args:
        min_words: Utterances shorter than this are rejected unless they are a
            known short command or a wake phrase.
        min_chars: Same idea, measured in letters, to catch "a b".
        min_confidence: STT confidence floor. Deepgram reports low confidence on
            noise long before the text itself looks suspicious.
        echo_similarity: How close a transcript must be to something VoxOS just
            said before it is treated as speaker bleed rather than a command.
        echo_memory: How many recent bot utterances to compare against.
    """

    min_words: int = 2
    min_chars: int = 4
    min_confidence: float = 0.35
    echo_similarity: float = 0.8
    echo_memory: int = 3
    _recent_bot_utterances: list[str] = field(default_factory=list, repr=False)

    def note_bot_utterance(self, text: str) -> None:
        """Remember what VoxOS just said so its own echo can be recognised."""
        normalized = " ".join(_tokens(text))
        if not normalized:
            return
        self._recent_bot_utterances.append(normalized)
        del self._recent_bot_utterances[: -self.echo_memory]

    def accept(self, text: str, confidence: float = 1.0) -> bool:
        """Return True when the transcript should reach the agent pipeline."""
        return self.reject_reason(text, confidence) is None

    def reject_reason(self, text: str, confidence: float = 1.0) -> str | None:
        """Return why the transcript was rejected, or None if it is acceptable.

        The reason string is stable and meant for logging and metrics, so the
        noise floor can be tuned from real sessions instead of guesses.
        """
        stripped = (text or "").strip()
        if not stripped:
            return "empty"

        if _NON_SPEECH_MARKER.match(stripped):
            return "non_speech"

        tokens = _tokens(stripped)
        if not tokens:
            return "no_speech"

        if confidence < self.min_confidence:
            return "low_confidence"

        joined = " ".join(tokens)

        if self._is_wake_phrase(tokens):
            return None

        if self._is_self_echo(joined):
            return "self_echo"

        if joined in _FILLER_PHRASES or _HESITATION.match(joined):
            return "filler"

        if len(tokens) == 1 and tokens[0] in _SHORT_COMMANDS:
            return None

        if len(tokens) < self.min_words or len(joined.replace(" ", "")) < self.min_chars:
            return "too_short"

        return None

    def _is_wake_phrase(self, tokens: list[str]) -> bool:
        if len(tokens) == 1:
            return tokens[0] in _WAKE_NAMES
        if len(tokens) == 2:
            return tokens[0] in _WAKE_PREFIXES and tokens[1] in _WAKE_NAMES
        return False

    def _is_self_echo(self, joined: str) -> bool:
        for said in self._recent_bot_utterances:
            ratio = SequenceMatcher(None, joined, said).ratio()
            if ratio >= self.echo_similarity:
                return True
        return False
