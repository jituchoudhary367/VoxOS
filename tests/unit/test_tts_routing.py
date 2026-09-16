"""Tests for choosing which voice speaks a reply.

Deepgram Aura streams into the pipeline and is the fast path, but it speaks
English only. Hindi and Hinglish replies fall back to edge-tts, which covers
the Indian-language voices in the voice map.
"""

from __future__ import annotations

import pytest

from voxos.voice.tts.routing import TTSRoute, choose_tts_route


def test_english_reply_streams_through_deepgram() -> None:
    assert choose_tts_route("Chrome is open now.") is TTSRoute.STREAMING


def test_devanagari_reply_falls_back_to_edge() -> None:
    assert choose_tts_route("क्रोम खुल गया है") is TTSRoute.FALLBACK


def test_hinglish_reply_falls_back_to_edge() -> None:
    assert choose_tts_route("bhai chrome kholo abhi") is TTSRoute.FALLBACK


def test_undetectable_text_uses_the_fast_path() -> None:
    assert choose_tts_route("...") is TTSRoute.STREAMING


def test_empty_reply_uses_the_fast_path() -> None:
    assert choose_tts_route("") is TTSRoute.STREAMING


def test_streaming_can_be_disabled_entirely() -> None:
    assert choose_tts_route("Chrome is open now.", streaming_enabled=False) is TTSRoute.FALLBACK


def test_disabling_streaming_still_routes_hindi_to_the_fallback() -> None:
    assert choose_tts_route("क्रोम खुल गया", streaming_enabled=False) is TTSRoute.FALLBACK


@pytest.mark.parametrize(
    "reply",
    [
        "Opened Notepad.",
        "I found three results for pizza near you.",
        "Sorry, I could not reach that page.",
    ],
)
def test_ordinary_english_replies_all_take_the_fast_path(reply: str) -> None:
    assert choose_tts_route(reply) is TTSRoute.STREAMING
