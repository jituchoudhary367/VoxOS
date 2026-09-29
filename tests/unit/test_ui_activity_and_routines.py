"""Live agent steps, the usage log, and routine scheduling."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from voxos.ui.activity import ActivityLog, Turn, describe, tool_words
from voxos.ui.routines import Routine, RoutineStore


# ── describe() ───────────────────────────────────────────────────────────────

def test_tool_start_and_finish_share_one_row() -> None:
    start = describe("tool_started", {"step_id": "s1", "tool": "system.get_resources"})
    done = describe("tool_completed", {"step_id": "s1", "tool": "system.get_resources",
                                        "success": True, "duration_ms": 420})
    assert start.key == done.key
    assert start.state == "running" and start.text.endswith("…")
    assert done.state == "done" and done.detail == "0.4 s"


def test_opening_an_app_names_the_app() -> None:
    step = describe("tool_completed", {"step_id": "s", "tool": "desktop.open_app",
                                        "args": {"app_name": "notepad"}, "success": True})
    assert step.text == "Opened Notepad"


def test_failures_and_unknown_tools_read_naturally() -> None:
    assert describe("tool_failed", {"tool": "web.search", "error": "timeout"}).state == "failed"
    assert tool_words("weird.do_thing") == ("Running do thing", "Ran do thing")


def test_chat_intents_and_noise_are_hidden() -> None:
    assert describe("intent_detected", {"intent": "general_conversation"}) is None
    assert describe("tool_requested", {"tool": "x"}) is None
    assert describe("intent_detected", {"intent": "open_application"}).text == "Understood: open application"


def test_plan_counts_steps() -> None:
    assert describe("plan_created", {"steps": 3}).text == "Planned 3 steps"
    assert describe("plan_created", {"steps": 1}).text == "Planned 1 step"


# ── ActivityLog ──────────────────────────────────────────────────────────────

def test_log_summarises_today_and_survives_reload(tmp_path: Path) -> None:
    now = datetime(2026, 9, 29, 15, 30).timestamp()
    log = ActivityLog(tmp_path / "a.jsonl")
    log.add(Turn(started=now - 60, seconds=1.2, kind="success", tools=["a", "b"]))
    log.add(Turn(started=now - 30, seconds=3.0, kind="error"))
    log.add(Turn(started=now - 86400, seconds=9.0, kind="success"))  # yesterday

    again = ActivityLog(tmp_path / "a.jsonl")
    s = again.summary(now)
    assert s["requests"] == 2
    assert s["success_rate"] == 0.5
    assert s["tools"] == 2
    assert s["per_hour"][15] == 2
    assert s["median_seconds"] == 3.0


def test_log_ignores_garbage_lines(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text("not json\n{\"started\": 1}\n", encoding="utf-8")
    assert ActivityLog(path).turns == []


# ── routines ─────────────────────────────────────────────────────────────────

MON_9 = datetime(2026, 9, 28, 9, 0)  # a Monday


def test_due_at_its_time_on_its_days_only() -> None:
    r = Routine("News", "news", "09:00", days=[0])
    assert r.is_due(MON_9)
    assert r.is_due(MON_9 + timedelta(minutes=90))  # within grace
    assert not r.is_due(MON_9 - timedelta(minutes=1))
    assert not r.is_due(MON_9 + timedelta(hours=3))  # missed long ago
    assert not r.is_due(MON_9 + timedelta(days=1))   # Tuesday


def test_runs_once_a_day(tmp_path: Path) -> None:
    store = RoutineStore(tmp_path / "r.json")
    r = store.add(Routine("News", "news", "09:00", days=[0]))
    assert store.due(MON_9) == [r]
    store.mark_ran(r, MON_9)
    assert store.due(MON_9 + timedelta(minutes=5)) == []
    assert RoutineStore(tmp_path / "r.json").items[0].last_run.startswith("2026-09-28")


def test_disabled_routines_never_run() -> None:
    assert not Routine("x", "x", "09:00", days=[0], enabled=False).is_due(MON_9)


def test_next_run_skips_to_the_next_scheduled_day() -> None:
    r = Routine("Fri", "x", "08:00", days=[4])
    assert r.next_run(MON_9) == datetime(2026, 10, 2, 8, 0)


def test_schedule_text_is_human() -> None:
    assert Routine("a", "b", "09:00", days=[0, 1, 2, 3, 4]).schedule_text() == "Weekdays at 9:00 AM"
    assert Routine("a", "b", "18:30", days=[5, 6]).schedule_text() == "Weekends at 6:30 PM"


# ── the live step card pairs starts and finishes ─────────────────────────────

def test_finish_without_step_id_closes_its_start_and_repeats_merge():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from voxos.ui.cards import ActivityCard

    card = ActivityCard()
    card.add_step(describe("tool_started", {"step_id": "g1_s1", "tool": "browser.open"}))
    card.add_step(describe("tool_completed", {"tool": "browser.open", "success": True}))  # old payload: no id
    assert len(card._rows) == 1 and next(iter(card._rows.values())).icon.state == "done"

    for i in range(3):
        card.add_step(describe("tool_started", {"step_id": f"r{i}", "tool": "browser.extract"}))
        card.add_step(describe("tool_completed", {"step_id": f"r{i}", "tool": "browser.extract", "success": True}))
    rows = list(card._rows.values())
    assert len(rows) == 2 and rows[1].text.text() == "Read the page  ×3"

    card.add_step(describe("tool_started", {"step_id": "x", "tool": "browser.search"}))
    card.finish(ok=False)
    assert list(card._rows.values())[-1].icon.state == "stopped"   # unknown, not "failed"


def test_executor_finish_event_carries_the_step_id():
    import inspect

    from voxos.agents.executor import agent

    source = inspect.getsource(agent.ExecutorAgent)
    assert '"step_id": step.id,' in source.split("event_type = EventType.TOOL_COMPLETED")[1][:600]


def test_failed_goal_reason_reads_as_its_own_sentence():
    from voxos.orchestration.synthesizer import _fallback
    from voxos.orchestration.worker import WorkerResult

    text = _fallback([WorkerResult(goal_id="g1", goal="Search Amazon for laptops", status="failed",
                                   error="My AI service is busy (Groq per-minute limit on all keys). Try again in a minute.")])
    assert text == ("I couldn't finish “Search Amazon for laptops”. My AI service is busy "
                    "(Groq per-minute limit on all keys). Try again in a minute.")
