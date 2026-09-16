"""
Language detection helpers for the Listener Agent.

Provides fast heuristic language detection for Hindi/English/Hinglish
without requiring an LLM call.
"""

from __future__ import annotations

import re


# ─────────────────────────────────────────────────────────────────────────────
# Language signatures
# ─────────────────────────────────────────────────────────────────────────────

# Hindi Devanagari Unicode range: \u0900–\u097F
DEVANAGARI_PATTERN = re.compile(r'[\u0900-\u097F]')

# Common Hindi romanization (Hinglish) words
HINDI_ROMANIZED_WORDS = {
    "kya", "hai", "karo", "bolo", "batao", "dekho", "mujhe", "mera",
    "meri", "tum", "aap", "yaha", "waha", "abhi", "kal", "aaj",
    "samay", "time", "kholo", "bandh", "chalao", "dikhao", "lao",
    "nahi", "haan", "thik", "acha", "shukriya", "dhanyawad",
    "vox", "voxos", "bhai", "yar", "dosto",
}


def detect_language(text: str) -> str:
    """
    Detect language from text. Returns BCP-47 language code.

    Returns:
        "hi"   — Hindi (Devanagari script)
        "hi-Latn" — Hinglish (Hindi in Latin script)
        "en"   — English
        "auto" — Cannot determine
    """
    if not text or not text.strip():
        return "auto"

    text_stripped = text.strip()

    # Devanagari script → definitely Hindi
    if DEVANAGARI_PATTERN.search(text_stripped):
        return "hi"

    # Check romanized Hindi words
    words = set(re.findall(r'\b\w+\b', text_stripped.lower()))
    hindi_word_count = len(words & HINDI_ROMANIZED_WORDS)
    total_words = len(words)

    if total_words > 0:
        hindi_ratio = hindi_word_count / total_words
        if hindi_ratio >= 0.3:
            return "hi-Latn"  # Hinglish
        if hindi_ratio > 0 and total_words <= 4:
            return "hi-Latn"  # Short mixed sentence

    return "en"


def normalize_language_code(code: str) -> str:
    """
    Normalize a language code from any format to a standard short form.

    Maps "hi-Latn" → "hi", "en-US" → "en", etc.
    """
    if not code or code == "auto":
        return "en"  # Default to English
    base = code.split("-")[0].lower()
    SUPPORTED = {"en", "hi", "fr", "de", "es", "zh", "ar", "pt", "ru", "ja"}
    return base if base in SUPPORTED else "en"


def is_multilingual(text: str) -> bool:
    """Return True if the text appears to mix multiple languages."""
    words = set(re.findall(r'\b\w+\b', text.lower()))
    has_hindi = bool(words & HINDI_ROMANIZED_WORDS) or bool(DEVANAGARI_PATTERN.search(text))
    has_english = bool(words - HINDI_ROMANIZED_WORDS)
    return has_hindi and has_english
