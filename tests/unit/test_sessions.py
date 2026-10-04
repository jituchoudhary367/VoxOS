"""Conversations survive a restart: kept on disk, reopened on launch."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxos.core.sessions import LocalFirstHistory, SessionStore


def test_current_session_is_reused_across_restarts(tmp_path: Path) -> None:
    first = SessionStore(tmp_path)
    sid = first.current()
    first.append(sid, "user", "find me a gaming laptop under 60k")
    first.append(sid, "assistant", "Here are three options.")

    again = SessionStore(tmp_path)          # a new terminal / app launch
    assert again.current() == sid
    assert [t.content for t in again.turns(sid)] == ["find me a gaming laptop under 60k", "Here are three options."]
    assert again.sessions[sid].title == "find me a gaming laptop under 60k"


def test_new_chat_keeps_the_old_one_in_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    old = store.current()
    store.append(old, "user", "hello")
    new = store.new()
    assert new != old and store.current() == new
    assert [s.id for s in store.recent()] == [old]      # empty new chat isn't listed yet
    assert store.new() == new                            # no pile of empty chats
    store.switch(old)
    assert SessionStore(tmp_path).current() == old


def test_delete_removes_transcript(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    sid = store.current()
    store.append(sid, "user", "x")
    store.delete(sid)
    assert sid not in store.sessions and store.turns(sid) == []
    assert store.current() != sid


def test_corrupt_index_starts_fresh(tmp_path: Path) -> None:
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    assert SessionStore(tmp_path).current()


@pytest.mark.asyncio
async def test_local_first_history_reads_from_disk_and_copies_to_db(tmp_path: Path) -> None:
    import asyncio

    copied: list[tuple] = []

    class Remote:
        async def append_turn(self, session_id, *, role, content):
            copied.append((session_id, role, content))

    store = SessionStore(tmp_path)
    history = LocalFirstHistory(store, Remote())
    await history.append_turn("s1", role="user", content="hi")
    await asyncio.sleep(0)
    recent = await history.list_recent("s1")
    assert [(t.role, t.content) for t in recent] == [("user", "hi")]
    assert copied == [("s1", "user", "hi")]


@pytest.mark.asyncio
async def test_database_down_never_loses_the_turn(tmp_path: Path) -> None:
    import asyncio

    class Down:
        async def append_turn(self, *a, **k):
            raise TimeoutError

    history = LocalFirstHistory(SessionStore(tmp_path), Down())
    await history.append_turn("s1", role="user", content="still saved")
    await asyncio.sleep(0)
    assert (await history.list_recent("s1"))[0].content == "still saved"


def test_window_reopens_the_conversation(tmp_path: Path, monkeypatch) -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from voxos.core.events.bus import get_event_bus
    from voxos.core.sessions import get_sessions
    from voxos.ui.dashboard import InteractionDashboard
    from voxos.ui.widgets import ChatBubble

    store = get_sessions()
    sid = store.current()
    store.append(sid, "user", "what's my battery?")
    store.append(sid, "assistant", "Battery is at 70%.")
    w = InteractionDashboard(env_path=str(tmp_path / ".env"), session_id=sid)
    try:
        assert len(w.chat_page.findChildren(ChatBubble)) == 2
        assert w.chat_page.empty.isHidden()
    finally:
        get_event_bus().unsubscribe_all(w._on_event)
        w.pill.close()
        w.close()
