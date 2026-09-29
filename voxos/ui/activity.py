"""
What the agents are doing, in words a person reads — and a record of it.

describe() turns an internal bus event into one line of the live timeline
shown under a request ("Opening Chrome…" → "Opened Chrome · 0.4 s").
ActivityLog keeps one row per finished request in the user's data folder,
which the Usage page reads for "today" and for response times.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Doing / done wording for the tools people see most; others are derived.
_TOOL_WORDS: dict[str, tuple[str, str]] = {
    "system.get_resources": ("Reading battery, CPU and memory", "Read system stats"),
    "system.get_info": ("Reading system info", "Read system info"),
    "system.get_time": ("Checking the time", "Checked the time"),
    "web.search": ("Searching the web", "Searched the web"),
    "web.extract": ("Reading the page", "Read the page"),
    "browser.open": ("Opening the browser", "Opened the browser"),
    "browser.search": ("Searching in the browser", "Searched in the browser"),
    "browser.extract": ("Reading the page", "Read the page"),
    "browser.click": ("Clicking on the page", "Clicked"),
    "browser.type": ("Typing on the page", "Typed"),
    "browser.new_tab": ("Opening a tab", "Opened a tab"),
    "browser.youtube_search": ("Searching YouTube", "Searched YouTube"),
    "browser.youtube_play": ("Starting the video", "Started the video"),
    "browser.media": ("Controlling playback", "Controlled playback"),
    "browser.fill_credential": ("Signing in with your saved login", "Signed in"),
    "vision.look": ("Looking at your screen", "Looked at your screen"),
    "desktop.open_app": ("Opening the app", "Opened the app"),
    "desktop.close_app": ("Closing the app", "Closed the app"),
    "desktop.screenshot": ("Taking a screenshot", "Took a screenshot"),
    "desktop.type_text": ("Typing", "Typed"),
    "os.read_file": ("Reading the file", "Read the file"),
    "os.write_file": ("Writing the file", "Wrote the file"),
    "os.list_dir": ("Looking in the folder", "Listed the folder"),
    "os.search_files": ("Searching your files", "Searched your files"),
    "os.delete_file": ("Deleting the file", "Deleted the file"),
    "os.shell_command": ("Running a command", "Ran a command"),
    "memory.save": ("Remembering that", "Remembered"),
    "memory.search": ("Checking what I remember", "Checked memory"),
    "code.python_repl": ("Running Python", "Ran Python"),
}


def tool_words(tool: str) -> tuple[str, str]:
    """(while running, when done) for a tool name."""
    if tool in _TOOL_WORDS:
        return _TOOL_WORDS[tool]
    words = tool.split(".", 1)[-1].replace("_", " ")
    return f"Running {words}", f"Ran {words}"


def _app_name(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("app_name", "name", "application", "query", "url"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:40]
    return ""


@dataclass(frozen=True)
class Step:
    key: str     # steps with the same key update one row
    text: str
    state: str   # running | done | failed | info | stopped
    detail: str = ""
    tool: str = ""  # lets a finish without a step id find its start


def describe(event_type: str, payload: dict[str, Any] | None, tool: str | None = None) -> Step | None:
    """One timeline row for a bus event, or None for events people needn't see."""
    p = payload or {}
    t = str(event_type)
    if t == "intent_detected":
        intent = str(p.get("intent") or "").replace("_", " ").strip()
        if not intent or intent == "general conversation":
            return None
        return Step("understand", f"Understood: {intent}", "done")
    if t == "plan_created":
        steps = p.get("steps")
        n = steps if isinstance(steps, int) else len(steps or []) or None
        return Step("plan", f"Planned {n} step{'s' if n != 1 else ''}" if n else "Made a plan", "done")
    if t in ("tool_started", "tool_completed", "tool_failed"):
        name = str(p.get("tool") or tool or "")
        if not name:
            return None
        key = f"tool:{p.get('step_id') or name}"
        doing, done = tool_words(name)
        target = _app_name(p.get("args"))
        if name in ("desktop.open_app", "desktop.close_app") and target:
            verb = ("Opening", "Opened") if name == "desktop.open_app" else ("Closing", "Closed")
            doing, done = f"{verb[0]} {target.title()}", f"{verb[1]} {target.title()}"
        if t == "tool_started":
            return Step(key, doing + "…", "running", tool=name)
        if t == "tool_failed" or p.get("success") is False:
            return Step(key, doing, "failed", str(p.get("error") or "failed")[:80], tool=name)
        ms = p.get("duration_ms")
        detail = f"{ms / 1000:.1f} s" if isinstance(ms, (int, float)) and ms >= 100 else ""
        return Step(key, done, "done", detail, tool=name)
    if t == "verification_passed":
        return Step("verify", "Checked the result", "done")
    if t == "verification_failed":
        return Step("verify", "Result didn't check out — retrying", "failed")
    if t == "task_replanned":
        return Step("replan", "Trying another way", "info")
    if t == "approval_required":
        return Step("approval", "Waiting for your approval", "running")
    if t == "approval_granted":
        return Step("approval", "You approved", "done")
    if t in ("approval_rejected", "approval_expired"):
        return Step("approval", "Not approved — stopped", "failed")
    return None


# ── the log behind the Usage page ────────────────────────────────────────────

@dataclass
class Turn:
    started: float                 # unix time
    seconds: float
    kind: str                      # success | error | info
    source: str = "chat"           # chat | voice | routine | screen
    steps: int = 0
    tools: list[str] = field(default_factory=list)

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.started).strftime("%Y-%m-%d")

    @property
    def hour(self) -> int:
        return datetime.fromtimestamp(self.started).hour


class ActivityLog:
    """Append-only JSON-lines file of finished requests, trimmed to the last 30 days."""

    KEEP_DAYS = 30

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.turns: list[Turn] = []
        if self.path.exists():
            cutoff = time.time() - self.KEEP_DAYS * 86400
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    turn = Turn(**json.loads(line))
                except (ValueError, TypeError):
                    continue
                if turn.started >= cutoff:
                    self.turns.append(turn)

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn)) + "\n")

    def compact(self) -> None:
        """Rewrite the file with only the kept rows."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(asdict(t)) + "\n" for t in self.turns), encoding="utf-8")
        os.replace(tmp, self.path)

    def today(self, now: float | None = None) -> list[Turn]:
        day = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
        return [t for t in self.turns if t.day == day]

    def summary(self, now: float | None = None) -> dict[str, Any]:
        today = self.today(now)
        done = [t for t in today if t.kind != "error"]
        seconds = sorted(t.seconds for t in today)
        median = seconds[len(seconds) // 2] if seconds else None
        per_hour = [0] * 24
        for t in today:
            per_hour[t.hour] += 1
        return {
            "requests": len(today),
            "success_rate": (len(done) / len(today)) if today else None,
            "median_seconds": median,
            "tools": sum(len(t.tools) for t in today),
            "per_hour": per_hour,
            "recent_seconds": [t.seconds for t in self.turns[-20:]],
        }


_log: ActivityLog | None = None


def get_activity_log() -> ActivityLog:
    global _log
    if _log is None:
        from voxos.core.profile.store import data_dir

        _log = ActivityLog(data_dir() / "activity.jsonl")
    return _log
