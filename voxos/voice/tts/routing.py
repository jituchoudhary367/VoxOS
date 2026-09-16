"""
Choosing which voice speaks a reply.

Deepgram Aura streams straight into the Pipecat pipeline, so audio starts
playing while the rest is still being generated — it is the fast path and
handles most replies. It speaks English only, so Hindi and Hinglish fall back
to edge-tts, which covers the Indian-language voices in the voice map.
"""

from __future__ import annotations

from enum import Enum

from voxos.agents.listener.language import detect_language

# Language codes Deepgram Aura cannot speak.
_FALLBACK_LANGUAGES = frozenset({"hi", "hi-Latn"})


class TTSRoute(Enum):
    """Which voice path a reply should take.

    Attributes:
        STREAMING: Deepgram Aura, streamed through the pipeline.
        FALLBACK: edge-tts, synthesized in full and played out of band.
    """

    STREAMING = "streaming"
    FALLBACK = "fallback"


def choose_tts_route(text: str, streaming_enabled: bool = True) -> TTSRoute:
    """Pick the voice path for a reply.

    Args:
        text: The reply Vox is about to speak.
        streaming_enabled: False when Deepgram TTS is not configured, which
            sends everything to edge-tts.

    Returns:
        The route to use. Text whose language cannot be determined takes the
        fast path, since undetectable text is almost always short English.
    """
    if detect_language(text) in _FALLBACK_LANGUAGES:
        return TTSRoute.FALLBACK
    if not streaming_enabled:
        return TTSRoute.FALLBACK
    return TTSRoute.STREAMING
