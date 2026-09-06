"""
Entity extraction helpers for the Listener Agent.

Provides pattern-based extraction for common entity types
(file paths, URLs, app names, time expressions, etc.)
to reduce LLM calls for obvious cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedEntities:
    """Extracted entities from a user utterance."""
    application: str | None = None
    file_path: str | None = None
    url: str | None = None
    query: str | None = None
    command: str | None = None
    directory: str | None = None
    time_expression: str | None = None
    quantity: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# Common app name normalization
APP_ALIASES: dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "firefox": "Mozilla Firefox",
    "edge": "Microsoft Edge",
    "notepad": "Notepad",
    "notepad++": "Notepad++",
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
    "teams": "Microsoft Teams",
    "spotify": "Spotify",
    "vlc": "VLC media player",
    "explorer": "File Explorer",
    "cmd": "Command Prompt",
    "terminal": "Windows Terminal",
    "powershell": "PowerShell",
    "task manager": "Task Manager",
    "calculator": "Calculator",
    "paint": "Microsoft Paint",
    "discord": "Discord",
    "zoom": "Zoom",
    "slack": "Slack",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
}


def normalize_app_name(raw: str) -> str:
    """Normalize an application name to its canonical form."""
    return APP_ALIASES.get(raw.lower().strip(), raw.strip())


def extract_url(text: str) -> str | None:
    """Extract the first URL from text."""
    pattern = r'https?://[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None


def extract_file_path(text: str) -> str | None:
    """Extract a Windows or Unix file path from text."""
    # Windows absolute path
    win_path = re.search(r'[A-Za-z]:\\[^\s"\']+', text)
    if win_path:
        return win_path.group(0)
    # Unix-style path
    unix_path = re.search(r'/[a-zA-Z0-9_./\-]+', text)
    if unix_path:
        return unix_path.group(0)
    # Relative path with extension
    rel_path = re.search(r'\b[\w./\\-]+\.\w{2,5}\b', text)
    if rel_path:
        return rel_path.group(0)
    return None


def extract_search_query(text: str, intent: str) -> str | None:
    """Extract the search query for web_search intent."""
    patterns = [
        r'search (?:for |about )?["\']?(.+?)["\']?$',
        r'(?:google|find|look up) ["\']?(.+?)["\']?$',
        r'what is ["\']?(.+?)["\']?$',
        r'who is ["\']?(.+?)["\']?$',
        r'how to ["\']?(.+?)["\']?$',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    if intent in {"search_web", "research_topic"}:
        return text.strip()
    return None


def enrich_entities(
    raw_entities: dict[str, Any],
    text: str,
    intent: str,
) -> dict[str, Any]:
    """
    Enrich LLM-extracted entities with pattern-based extraction.
    
    The LLM does the heavy lifting; this adds high-confidence structural extraction.
    """
    enriched = dict(raw_entities)

    # Normalize app name if present
    if "application" in enriched:
        enriched["application"] = normalize_app_name(str(enriched["application"]))

    # Fill missing URL
    if "url" not in enriched:
        url = extract_url(text)
        if url:
            enriched["url"] = url

    # Fill missing file path
    if "file_path" not in enriched and "path" not in enriched:
        fp = extract_file_path(text)
        if fp and intent in {"read_file", "create_file", "delete_file", "move_file", "list_files"}:
            enriched["file_path"] = fp

    # Fill missing query
    if "query" not in enriched and intent in {"search_web", "research_topic"}:
        q = extract_search_query(text, intent)
        if q:
            enriched["query"] = q

    return enriched
