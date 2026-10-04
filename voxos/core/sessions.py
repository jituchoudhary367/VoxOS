"""
Conversations that survive a restart.

Every launch used to start a brand-new random session, and history lived only
in the cloud database (often asleep, 5-15 s to wake), so a new terminal meant
Vox had forgotten the chat. Now each conversation is a session kept on this
PC — an index plus one JSON-lines transcript per session in the user's data
folder — and the last one is reopened on launch.

LocalFirstHistory is what the agents read and write: turns land on disk at
once and are read from disk (instant, offline), while a copy still goes to
the database in the background for anything that needs it there.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)

TITLE_CHARS = 60
KEEP_SESSIONS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SessionInfo:
    id: str
    title: str = "New chat"
    created: str = ""
    updated: str = ""
    turns: int = 0


@dataclass
class Turn:
    role: str
    content: str
    at: str = ""


class SessionStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.index_path = self.folder / "index.json"
        self.sessions: dict[str, SessionInfo] = {}
        self.current_id: str | None = None
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                for raw in data.get("sessions", []):
                    info = SessionInfo(**raw)
                    self.sessions[info.id] = info
                self.current_id = data.get("current")
            except (OSError, ValueError, TypeError) as e:
                log.warning("sessions_index_unreadable_starting_fresh", error=str(e))

    # ── which conversation ───────────────────────────────────────────────────

    def current(self) -> str:
        """The conversation to continue: the last one used, or a new one."""
        if self.current_id and self.current_id in self.sessions:
            return self.current_id
        return self.new()

    def new(self) -> str:
        """Start a fresh conversation, unless the current one is still empty."""
        if self.current_id and self.current_id in self.sessions and self.sessions[self.current_id].turns == 0:
            return self.current_id
        sid = str(uuid.uuid4())
        now = _now()
        self.sessions[sid] = SessionInfo(id=sid, created=now, updated=now)
        self.current_id = sid
        self._save_index()
        return sid

    def switch(self, session_id: str) -> str:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        self.current_id = session_id
        self._save_index()
        return session_id

    def recent(self, limit: int = 30) -> list[SessionInfo]:
        """Conversations with something in them, newest first."""
        items = [s for s in self.sessions.values() if s.turns > 0]
        return sorted(items, key=lambda s: s.updated, reverse=True)[:limit]

    def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        try:
            self._transcript(session_id).unlink(missing_ok=True)
        except OSError:
            pass
        if self.current_id == session_id:
            self.current_id = None
        self._save_index()

    # ── what was said ────────────────────────────────────────────────────────

    def append(self, session_id: str, role: str, content: str) -> None:
        info = self.sessions.get(session_id)
        if info is None:  # a session started elsewhere (API, an older launch)
            info = self.sessions[session_id] = SessionInfo(id=session_id, created=_now())
        turn = Turn(role=role, content=content, at=_now())
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self._transcript(session_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(turn), ensure_ascii=False) + "\n")
        info.turns += 1
        info.updated = turn.at
        if role == "user" and info.title == "New chat":
            title = " ".join(content.split())
            info.title = title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")
        self._trim()
        self._save_index()

    def turns(self, session_id: str, limit: int | None = None) -> list[Turn]:
        path = self._transcript(session_id)
        if not path.exists():
            return []
        out: list[Turn] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(Turn(**json.loads(line)))
            except (ValueError, TypeError):
                continue
        return out[-limit:] if limit else out

    # ── files ────────────────────────────────────────────────────────────────

    def _transcript(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c == "-")
        return self.folder / f"{safe}.jsonl"

    def _trim(self) -> None:
        if len(self.sessions) <= KEEP_SESSIONS:
            return
        oldest = sorted(self.sessions.values(), key=lambda s: s.updated)[: len(self.sessions) - KEEP_SESSIONS]
        for info in oldest:
            if info.id != self.current_id:
                self.delete(info.id)

    def _save_index(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        data = {"current": self.current_id, "sessions": [asdict(s) for s in self.sessions.values()]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index_path)


class LocalFirstHistory:
    """ConversationHistory: disk first (instant, offline), the database as a background copy."""

    def __init__(self, store: SessionStore | None = None, remote: Any | None = None) -> None:
        self._store = store or get_sessions()
        self._remote = remote

    async def append_turn(self, session_id: str, *, role: str, content: str) -> None:
        self._store.append(session_id, role, content)
        if self._remote is not None:
            asyncio.ensure_future(self._copy(session_id, role, content))

    async def _copy(self, session_id: str, role: str, content: str) -> None:
        try:
            await self._remote.append_turn(session_id, role=role, content=content)
        except Exception as e:  # noqa: BLE001 — the local copy is the one that matters
            log.debug("history_remote_copy_failed", error=str(e)[:200])

    async def list_recent(self, session_id: str, limit: int = 12) -> list[Turn]:
        return self._store.turns(session_id, limit=limit)


_store: SessionStore | None = None


def get_sessions() -> SessionStore:
    global _store
    if _store is None:
        from voxos.core.profile.store import data_dir

        _store = SessionStore(data_dir() / "sessions")
    return _store


def default_history() -> LocalFirstHistory:
    """What the app's conversation services use: local transcripts + a database copy."""
    try:
        from voxos.core.conversation.history import DatabaseConversationHistory

        remote = DatabaseConversationHistory()
    except Exception:  # noqa: BLE001 — no database configured: local only
        remote = None
    return LocalFirstHistory(get_sessions(), remote)
