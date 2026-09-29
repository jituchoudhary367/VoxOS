"""Live steps, approvals, one-tap answers, the voice pill, routines and usage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path: Path, monkeypatch):
    import voxos.ui.activity as activity
    import voxos.ui.routines as routines
    from voxos.core.events.bus import get_event_bus
    from voxos.ui.dashboard import InteractionDashboard

    monkeypatch.setattr(activity, "_log", None)
    monkeypatch.setattr(routines, "_store", None)
    w = InteractionDashboard(env_path=str(tmp_path / ".env"), session_id="s-1")
    yield w
    get_event_bus().unsubscribe_all(w._on_event)
    w.pill.close()
    w.close()
    w.deleteLater()


class _Event:
    def __init__(self, kind: str, payload: dict, session_id: str = "s-1", tool: str | None = None):
        from voxos.core.events.types import EventType

        self.event_type = EventType(kind)
        self.payload = payload
        self.session_id = session_id
        self.tool = tool


@pytest.mark.asyncio
async def test_agent_events_become_live_steps_then_a_usage_record(window) -> None:
    from voxos.ui.activity import get_activity_log

    window.chat_page.input.setText("how's my battery")
    window.send_chat_message()
    await window._on_event(_Event("intent_detected", {"intent": "get_resources"}))
    await window._on_event(_Event("tool_started", {"step_id": "a", "tool": "system.get_resources"}))
    card = window._activity
    assert card is not None and len(card._rows) == 2
    assert card._rows["tool:a"].icon.state == "running"

    await window._on_event(_Event("tool_completed", {"step_id": "a", "tool": "system.get_resources",
                                                     "success": True, "duration_ms": 110}))
    window.append_transcript("Battery is at 70% and charging.")
    assert card.finished and card._rows["tool:a"].icon.state == "done"
    turn = get_activity_log().turns[-1]
    assert turn.kind == "success" and turn.source == "chat" and turn.tools == ["system.get_resources"]


@pytest.mark.asyncio
async def test_other_sessions_events_are_ignored(window) -> None:
    await window._on_event(_Event("tool_started", {"step_id": "x", "tool": "web.search"}, session_id="other"))
    assert window._activity is None


def test_plain_chat_reply_leaves_no_empty_step_card(window) -> None:
    window.chat_page.input.setText("hello")
    window.send_chat_message()
    window.append_transcript("Hi — how can I help?")
    from voxos.ui.cards import ActivityCard
    assert window.chat_page.findChildren(ActivityCard) == []


def test_approval_card_decides_once(window, qapp) -> None:
    from voxos.ui.cards import ApprovalCard

    decided: list[tuple[str, bool]] = []
    window._on_decided = lambda aid, ok: decided.append((aid, ok))  # type: ignore[method-assign]
    window._show_approval({"id": "ap-1", "tool_name": "os.delete_file", "risk_level": "HIGH",
                           "consequences": ["Deletes a file"], "expires_at": "2099-01-01T00:00:00+00:00"})
    card = window.chat_page.findChildren(ApprovalCard)[-1]
    texts = [lbl.text() for lbl in card.findChildren(type(card.countdown))]
    assert "Approve this action?" in texts and "Deleting the file" in texts
    card.approve.click()
    assert decided == [("ap-1", True)]
    assert card.approve.isHidden() and card.countdown.text() == "Approved"


def _ask(window, questions, reply):
    from voxos.core.conversation import pending

    pending.remember("s-1", "buy a laptop", questions)
    window.append_transcript(reply)
    return window.chat_page.active_question


def test_question_card_replaces_the_question_text_and_sends_the_pick(window, qapp) -> None:
    from voxos.ui.widgets import ChatBubble

    sent: list[str] = []
    window.chat_message_sent.connect(sent.append)
    card = _ask(window, [{"question": "Which brand?", "options": ["HP", "Dell", "Lenovo"]}],
                "I found three brands under ₹60,000. Which brand? (HP, Dell or Lenovo)")
    assert card is not None and not card.done
    texts = [b.findChildren(type(window.status_label))[-1].text() for b in window.chat_page.findChildren(ChatBubble)]
    assert texts[-1] == "I found three brands under ₹60,000."   # the question moved into the card
    card._answer("Dell")
    assert sent == ["Dell"] and card.done
    assert all(c.done for c in window.voice_page.findChildren(type(card)))


def test_digit_in_empty_chat_box_picks_an_option(window, qapp) -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    card = _ask(window, [{"question": "Which brand?", "options": ["HP", "Dell"]}], "Which brand? (HP or Dell)")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier, "2")
    assert window.chat_page.eventFilter(window.chat_page.input, event) is True
    assert card._options[1].property("selected") is True


def test_several_questions_step_through_then_review(window) -> None:
    sent: list[str] = []
    window.chat_message_sent.connect(sent.append)
    card = _ask(window, [{"question": "Which brand?", "options": ["HP", "Dell"]},
                         {"question": "Budget?", "options": ["Under ₹50k", "Under ₹80k"]}],
                "I need to check a few things: 1. Which brand? (HP or Dell) 2. Budget? (Under ₹50k or Under ₹80k)")
    assert card.step.text() == "1 of 2"
    card._answer("HP")
    assert card.step.text() == "2 of 2"
    card._answer("Under ₹80k")
    assert card.step.text() == "Review" and not sent
    card._submit()
    assert sent == ["1. HP; 2. Under ₹80k"]


def test_typing_instead_retires_the_card(window) -> None:
    card = _ask(window, [{"question": "Shall I add it to your cart?", "options": []}],
                "Found it. Shall I add it to your cart?")
    assert [o["label"] for o in card.questions[0]["options"]] == ["Yes, go ahead", "No"]
    window.chat_page.input.setText("actually show me Dell instead")
    window.send_chat_message()
    assert card.done


def test_voice_pill_appears_when_the_window_is_away(window) -> None:
    window.set_voice_active(True)
    window.update_status("Vox: Listening...")
    assert window.pill.isVisible()
    assert window.pill.state.text() == "Listening…"
    window.append_transcript("User: open notepad")
    assert "open notepad" in window.pill.line.text()


def test_routines_page_lists_and_runs(window) -> None:
    from voxos.ui.routines import Routine, get_routines

    get_routines().add(Routine("News", "top news", "09:00", days=[0, 1, 2, 3, 4]))
    window.routines_page.refresh()
    ran: list = []
    window.routine_run_requested.connect(ran.append)
    run = next(b for b in window.routines_page.findChildren(type(window.chat_page.send_btn))
               if b.text() == "Run now")
    run.click()
    assert ran and ran[0].name == "News"


def test_usage_page_renders_today(window) -> None:
    import time

    from voxos.ui.activity import Turn, get_activity_log

    get_activity_log().add(Turn(started=time.time(), seconds=1.5, kind="success", tools=["web.search"]))
    window.usage_page.refresh()
    assert window.usage_page.t_requests.value.text() == "1"
    assert window.usage_page.t_success.value.text() == "100%"
    assert sum(window.usage_page.chart.values) == 1
