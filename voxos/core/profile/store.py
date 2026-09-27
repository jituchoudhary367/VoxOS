"""
The user profile: a small local JSON file Vox learns into.

- user_id: stable across sessions (the desktop app had none, so memories
  keyed by session were lost on every restart);
- facts: durable things the user said or chose ("Prefers HP laptops",
  "User's name is Abhijeet"), deduplicated, newest last, capped;
- habits: counters of tools, sites and active hours, from what tasks did.

It lives in the user's app-data folder and nowhere else; only its short
summary() goes into prompts.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)

_NAME = re.compile(r"^(?:the\s+)?user'?s name is\s+(.+?)\.?$", re.IGNORECASE)


def data_dir() -> Path:
    """Vox's per-user data folder (VOX_DATA_DIR overrides, e.g. for tests)."""
    override = os.environ.get("VOX_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "VoxOS"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserProfile:
    MAX_FACTS = 60
    HABIT_KINDS = ("tools", "sites", "hours")

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("profile_unreadable_starting_fresh", path=str(self.path), error=str(e))
                data = {}
        self.user_id: str = data.get("user_id") or str(uuid.uuid4())
        self.name: str | None = data.get("name")
        self.facts: list[dict[str, str]] = [
            f for f in data.get("facts", []) if isinstance(f, dict) and f.get("text")
        ]
        habits = data.get("habits", {})
        self.habits: dict[str, Counter] = {
            kind: Counter({str(k): int(v) for k, v in habits.get(kind, {}).items()})
            for kind in self.HABIT_KINDS
        }
        self.first_seen: str = data.get("first_seen") or _now()
        self.last_seen: str = data.get("last_seen") or self.first_seen

    # ── facts ────────────────────────────────────────────────────────────────

    def add_facts(self, texts: list[str]) -> int:
        """Add facts; a restated fact moves to the end (newest). Returns how many changed."""
        changed = 0
        for raw in texts:
            text = " ".join(str(raw).split()).strip()
            if not text or len(text) > 240:
                continue
            key = text.lower()
            self.facts = [f for f in self.facts if f["text"].lower() != key]
            self.facts.append({"text": text, "updated": _now()})
            if match := _NAME.match(text):
                self.name = match.group(1).strip()
            changed += 1
        del self.facts[:-self.MAX_FACTS]
        return changed

    def forget(self, pattern: str) -> int:
        """Drop facts containing `pattern` (case-insensitive). Returns how many."""
        needle = pattern.strip().lower()
        if not needle:
            return 0
        before = len(self.facts)
        self.facts = [f for f in self.facts if needle not in f["text"].lower()]
        if self.name and needle in self.name.lower():
            self.name = None
        return before - len(self.facts)

    # ── habits ───────────────────────────────────────────────────────────────

    def record_activity(self, *, tools: list[str], sites: list[str], hour: int | None = None) -> None:
        self.habits["tools"].update(tools)
        self.habits["sites"].update(s.lower().removeprefix("www.") for s in sites if s)
        if hour is None:
            hour = datetime.now().hour
        self.habits["hours"][str(hour)] += 1
        self.last_seen = _now()

    # ── output ───────────────────────────────────────────────────────────────

    def summary(self, max_facts: int = 15) -> str:
        """A few lines about the user for an LLM prompt; empty when nothing is known."""
        lines = []
        if self.name:
            lines.append(f"Name: {self.name}")
        lines += [f"- {f['text']}" for f in self.facts[-max_facts:]]
        sites = [s for s, _ in self.habits["sites"].most_common(5)]
        if sites:
            lines.append(f"Often uses: {', '.join(sites)}")
        hours = [f"{int(h):02d}:00" for h, _ in self.habits["hours"].most_common(3)]
        if sum(self.habits["hours"].values()) >= 3 and hours:
            lines.append(f"Usually active around: {', '.join(hours)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "facts": self.facts,
            "habits": {kind: dict(counter) for kind, counter in self.habits.items()},
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    def save(self) -> None:
        """Write atomically so a crash never leaves half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


_profile: UserProfile | None = None


def get_profile() -> UserProfile:
    global _profile
    if _profile is None:
        _profile = UserProfile(data_dir() / "profile.json")
        if not _profile.path.exists():
            _profile.save()  # fix the user id from the first run on
    return _profile
