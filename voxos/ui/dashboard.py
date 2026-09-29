"""
Vox's main window: one place for talking to Vox and for managing it.

Tabs: Chat, Voice, Routines (the assistant); Usage, Profile, Settings (you).
VoxApp talks to it through switch_mode(), update_status(), append_transcript()
and the chat/voice signals, plus show_page(), begin_request()
and set_voice_active(). The agents' own progress arrives straight from the
in-process event bus and is drawn live under each request.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from voxos.ui.activity import Turn, describe, get_activity_log
from voxos.ui.cards import ActivityCard, ApprovalCard, ImageBubble
from voxos.ui.pill import VoicePill
from voxos.ui.profile_page import ProfilePage
from voxos.ui.question_card import QuestionCard
from voxos.ui.routines_page import RoutinesPage
from voxos.ui.settings_page import SettingsPage
from voxos.ui.usage_page import UsagePage
from voxos.ui.theme import PALETTE, Fonts, Icon, stylesheet
from voxos.ui.widgets import (
    Aurora, ChatBubble, FadeStack, LogoMark, PageHeader, SideNav, StatusPill, Toast, TypingIndicator,
    VoiceOrb, button, fade_in, glyph, label, status_state,
)

PAGES = ("chat", "voice", "routines", "usage", "profile", "settings")

SUGGESTIONS = (
    "What's my battery level?",
    "Open Notepad",
    "Search the latest tech news",
    "Play lo-fi music on YouTube",
)


def message_role(text: str) -> tuple[str, str]:
    """Split a transcript line into (role, text). Role is user, assistant or system."""
    for prefix in ("You:", "User:"):
        if text.startswith(prefix):
            return "user", text[len(prefix):].strip()
    if text.startswith("VoxOS ["):
        return "system", text
    for prefix in ("VoxOS:", "Vox:"):
        if text.startswith(prefix):
            return "assistant", text[len(prefix):].strip()
    return "assistant", text.strip()


def _greeting(name: str | None) -> str:
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


def strip_questions(reply: str, questions: list[dict]) -> str:
    """The reply without the question sentence(s) at its end; the card shows those."""
    from voxos.orchestration.synthesizer import question_text

    text = reply.rstrip()
    for tail in (question_text(questions), " ".join(q.get("question", "") for q in questions)):
        tail = tail.strip()
        if tail and text.endswith(tail):
            return text[: -len(tail)].rstrip()
    return text


def _open_png(png: bytes) -> None:
    """Show a screenshot full size in the default image viewer."""
    path = os.path.join(tempfile.gettempdir(), "vox-screen.png")
    with open(path, "wb") as f:
        f.write(png)
    if hasattr(os, "startfile"):
        os.startfile(path)


class ChatPage(QWidget):
    send = Signal(str)
    look = Signal()          # "What's on my screen?"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        col = QVBoxLayout(self)
        col.setContentsMargins(44, 36, 44, 28)
        col.setSpacing(18)

        header = PageHeader("Chat", "Type what you need. Vox works on your computer and answers here.")
        clear = button("Clear", "ghost", "Clear this conversation from the window")
        clear.clicked.connect(self.clear)
        header.actions.addWidget(clear)
        col.addWidget(header)

        # messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.aurora = Aurora(self)
        self.aurora.lower()
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setAutoFillBackground(False)
        feed = QWidget()
        feed.setObjectName("feed")
        feed.setStyleSheet("QWidget#feed { background: transparent; }")
        self.feed = QVBoxLayout(feed)
        self.feed.setContentsMargins(4, 4, 12, 4)
        self.feed.setSpacing(10)
        self.feed.addStretch(1)
        self.empty = self._empty_state()
        self.feed.addWidget(self.empty)
        self.typing = TypingIndicator()
        self.typing.hide()
        typing_row = QHBoxLayout()
        typing_row.setContentsMargins(40, 0, 0, 0)
        typing_row.addWidget(self.typing)
        typing_row.addStretch(1)
        self.feed.addLayout(typing_row)
        self.scroll.setWidget(feed)
        col.addWidget(self.scroll, 1)
        self._scroll_anim = QPropertyAnimation(self.scroll.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(320)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._follow)
        self._stick = True

        # composer
        composer = QHBoxLayout()
        composer.setSpacing(10)
        self.input = QLineEdit()
        self.input.setObjectName("chat_input")
        self.input.setPlaceholderText("Ask Vox to do something…")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        self.active_question: QuestionCard | None = None
        self.send_btn = QPushButton(Icon.SEND)
        self.send_btn.setObjectName("send_btn")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._submit)
        self.look_btn = QPushButton(Icon.SCREEN)
        self.look_btn.setObjectName("look_btn")
        self.look_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.look_btn.setToolTip("What's on my screen? Vox takes a screenshot and tells you.")
        self.look_btn.setStyleSheet(
            f"QPushButton#look_btn {{ font-family: '{Fonts.icons}'; font-size: 16px; background: {PALETTE['surface']};"
            f" border: 1px solid {PALETTE['border']}; border-radius: 24px; color: {PALETTE['green']};"
            " min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px; padding: 0; }"
            f"QPushButton#look_btn:hover {{ background: {PALETTE['green_soft']}; border-color: {PALETTE['green']}; }}"
            f"QPushButton#look_btn:disabled {{ color: {PALETTE['text_faint']}; }}"
        )
        self.look_btn.clicked.connect(self.look.emit)
        composer.addWidget(self.look_btn)
        composer.addWidget(self.input, 1)
        composer.addWidget(self.send_btn)
        col.addLayout(composer)

    def _empty_state(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 30)
        col.setSpacing(10)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo = LogoMark(96, intro=True)
        col.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        self.hello = QLabel()
        self.hello.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hello.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 34px; color: {PALETTE['text']};")
        sub = label("What can Vox do for you?", "page_sub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.hello)
        col.addWidget(sub)
        col.addSpacing(12)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addStretch(1)
        for text in SUGGESTIONS:
            chip = button(text)
            chip.setObjectName("chip")
            chip.clicked.connect(lambda _=False, t=text: self._send_text(t))
            chips.addWidget(chip)
        chips.addStretch(1)
        col.addLayout(chips)
        return box

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.aurora.setGeometry(self.rect())

    def refresh_greeting(self) -> None:
        try:
            from voxos.core.profile.store import get_profile
            name = get_profile().name
        except Exception:  # noqa: BLE001
            name = None
        self.hello.setText(_greeting(name))

    def _submit(self) -> None:
        self._send_text(self.input.text())

    def _send_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.input.clear()
        self.add(text, "user")
        self.send.emit(text)

    def insert(self, widget: QWidget, rise: int = 12) -> QWidget:
        """Put a widget at the end of the conversation (above the typing dots)."""
        self.empty.hide()
        self.aurora.set_running(False)  # keep the glow, stop the drift once talking starts
        self.feed.insertWidget(self.feed.count() - 1, widget)
        self._stick = True
        fade_in(widget, 240, rise)
        return widget

    def add(self, text: str, role: str) -> None:
        if text:
            self.insert(ChatBubble(text, role))

    def add_indented(self, widget: QWidget) -> QWidget:
        """Cards that belong to Vox's side sit in line with its replies."""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(40, 0, 0, 0)
        row.addWidget(widget)
        row.addStretch(1)
        holder.card = widget  # type: ignore[attr-defined]
        return self.insert(holder, 8)

    def begin_activity(self) -> ActivityCard:
        card = ActivityCard()
        self.add_indented(card)
        return card

    def add_image(self, png: bytes, caption: str) -> ImageBubble:
        bubble = ImageBubble(png, caption)
        self.insert(bubble)
        return bubble

    def add_question(self, card: QuestionCard) -> QuestionCard:
        self.active_question = card
        self.add_indented(card)
        return card

    def eventFilter(self, obj, event):
        """With a question open and the chat box empty, 1-9 picks an option."""
        from PySide6.QtCore import QEvent

        card = self.active_question
        if (obj is self.input and event.type() == QEvent.Type.KeyPress and not self.input.text()
                and card is not None and not card.done and event.text().isdigit()):
            if card.pick_number(int(event.text())):
                return True
        return super().eventFilter(obj, event)

    def set_thinking(self, on: bool, dots: bool = True) -> None:
        if on and dots:
            self.typing.start()
            self._stick = True
        else:
            self.typing.stop()

    def clear(self) -> None:
        for i in reversed(range(self.feed.count())):
            w = self.feed.itemAt(i).widget()
            if w is not None and w is not self.empty:
                w.setParent(None)
                w.deleteLater()
        self.empty.show()
        self.aurora.set_running(True)
        self.logo.replay_intro()
        fade_in(self.empty, 260, 10)

    def _follow(self, _min: int, maximum: int) -> None:
        if not self._stick:
            return
        bar = self.scroll.verticalScrollBar()
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(maximum)
        self._scroll_anim.start()


class VoicePage(QWidget):
    toggle = Signal()

    def __init__(self, hotkey: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        col = QVBoxLayout(self)
        col.setContentsMargins(44, 36, 44, 36)
        col.setSpacing(8)
        col.addWidget(PageHeader("Voice", "Talk to Vox hands-free."))
        col.addStretch(1)

        self.orb = VoiceOrb()
        self.orb.clicked.connect(self.toggle.emit)
        col.addWidget(self.orb, 0, Qt.AlignmentFlag.AlignCenter)

        self.state_label = QLabel("Ready when you are")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 24px;")
        col.addWidget(self.state_label)

        self.heard = label("", "muted", wrap=True)
        self.heard.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heard.setMinimumHeight(22)
        self.reply = label("", wrap=True)
        self.reply.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reply.setStyleSheet(f"font-size: 15px; color: {PALETTE['green_dark']};")
        col.addWidget(self.heard)
        col.addWidget(self.reply)
        self.question_slot = QVBoxLayout()
        col.addLayout(self.question_slot)
        col.addSpacing(16)

        self.btn = button("Start listening", "primary")
        self.btn.setMinimumWidth(200)
        self.btn.setMinimumHeight(46)
        self.btn.setStyleSheet("border-radius: 23px; font-size: 15px;")
        self.btn.clicked.connect(self.toggle.emit)
        col.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignCenter)
        col.addSpacing(10)

        hint = QHBoxLayout()
        hint.addStretch(1)
        for key_text, rest in ((hotkey.replace("+", " + ").title(), "tap to talk hands-free, hold to push-to-talk"),
                               ("Wake word", "say it any time")):
            key = QLabel(key_text)
            key.setStyleSheet(
                f"background: {PALETTE['surface']}; border: 1px solid {PALETTE['border_strong']};"
                "border-bottom-width: 2px; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600;"
            )
            hint.addWidget(key)
            hint.addWidget(label(rest, "field_help"))
            hint.addSpacing(14)
        hint.addStretch(1)
        col.addLayout(hint)
        col.addStretch(1)

    _WORDS = {"idle": "Ready when you are", "listening": "Listening…", "thinking": "Thinking…",
              "speaking": "Speaking…", "error": "Something went wrong"}

    def set_status(self, text: str) -> None:
        state = status_state(text)
        self.orb.set_state(state)
        words = self._WORDS[state]
        if state == "idle" and "unavailable" in text.lower():
            words = "Wake word unavailable — use the hotkey"
        if self.state_label.text() != words:
            self.state_label.setText(words)
            fade_in(self.state_label, 220, 6)
        active = state in ("listening", "thinking", "speaking")
        self.btn.setText("Stop listening" if active else "Start listening")
        self.btn.setProperty("kind", "danger" if active else "primary")
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)

    def show_question(self, card: QuestionCard) -> None:
        while self.question_slot.count():
            item = self.question_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.question_slot.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        fade_in(card, 240, 10)

    def show_line(self, role: str, text: str) -> None:
        target = self.heard if role == "user" else self.reply
        target.setText(f"“{text}”" if role == "user" else text)
        fade_in(target, 260, 8)


class InteractionDashboard(QWidget):
    chat_message_sent = Signal(str)
    voice_toggle_requested = Signal()
    restart_requested = Signal()
    routine_run_requested = Signal(object)   # Routine
    voice_stop_requested = Signal()

    def __init__(self, env_path: str = ".env", session_id: str | None = None):
        super().__init__()
        Fonts.resolve()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet())

        self.setObjectName("root")
        self.setWindowTitle("VoxOS")
        self.setMinimumSize(1040, 700)
        self.resize(1180, 780)

        try:
            from voxos.config import get_settings
            hotkey = get_settings().voice.hotkey
        except Exception:  # noqa: BLE001
            hotkey = "ctrl+space"

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 28, 18, 22)
        side.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(10, 0, 0, 0)
        self.logo = LogoMark(44)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_col.addWidget(label("Vox", "brand"))
        brand_col.addWidget(label("Your desktop agent", "brand_sub"))
        brand_row.addWidget(self.logo)
        brand_row.addSpacing(8)
        brand_row.addLayout(brand_col, 1)
        side.addLayout(brand_row)
        side.addSpacing(26)

        self.nav = SideNav()
        self.nav.add_section("Assistant")
        self.nav.add(Icon.CHAT, "Chat")
        self.nav.add(Icon.MIC, "Voice")
        self.nav.add(Icon.ROUTINE, "Routines")
        self.nav.add_section("You")
        self.nav.add(Icon.CHART, "Usage")
        self.nav.add(Icon.PROFILE, "Profile")
        self.nav.add(Icon.SETTINGS, "Settings")
        side.addWidget(self.nav)
        side.addStretch(1)

        self.side_status = StatusPill()
        side.addWidget(self.side_status)
        side.addSpacing(10)
        tip = QFrame()
        tip.setStyleSheet(f"QFrame {{ background: {PALETTE['green_soft']}; border-radius: 12px; }}")
        tip_col = QVBoxLayout(tip)
        tip_col.setContentsMargins(14, 12, 14, 12)
        tip_col.setSpacing(4)
        tip_head = QHBoxLayout()
        tip_head.addWidget(glyph(Icon.MIC, 13, "green"))
        t = label("Quick talk")
        t.setStyleSheet(f"background: transparent; font-weight: 600; color: {PALETTE['green_dark']};")
        tip_head.addWidget(t)
        tip_head.addStretch(1)
        tip_col.addLayout(tip_head)
        tip_text = label(f"Press {hotkey.replace('+', ' + ').title()} anywhere to talk to Vox.", wrap=True)
        tip_text.setStyleSheet(f"background: transparent; font-size: 12px; color: {PALETTE['green_dark']};")
        tip_col.addWidget(tip_text)
        side.addWidget(tip)
        root.addWidget(sidebar)

        # pages
        self.stack = FadeStack()
        self.chat_page = ChatPage()
        self.voice_page = VoicePage(hotkey)
        self.routines_page = RoutinesPage()
        self.usage_page = UsagePage()
        self.profile_page = ProfilePage()
        self.settings_page = SettingsPage(env_path)
        for page in (self.chat_page, self.voice_page, self.routines_page, self.usage_page,
                     self.profile_page, self.settings_page):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self.toast = Toast(self)

        # wiring
        self.nav.changed.connect(self._on_nav)
        self.chat_page.send.connect(self.chat_message_sent.emit)
        self.voice_page.toggle.connect(self.voice_toggle_requested.emit)
        self.profile_page.notify.connect(self.toast.show_message)
        self.settings_page.notify.connect(self.toast.show_message)
        self.settings_page.restart_requested.connect(self.restart_requested.emit)
        self.routines_page.notify.connect(self.toast.show_message)
        self.routines_page.run_requested.connect(self.routine_run_requested.emit)
        self.chat_page.send.connect(self._typed)
        self.chat_page.look.connect(self._look_at_screen)

        # live agent steps, approvals and the voice pill
        self.session_id = session_id
        self._activity: ActivityCard | None = None
        self._turn: dict | None = None
        self._voice_active = False
        self._question_cards: list[QuestionCard] = []
        self._asked_at: float | None = None
        self.pill = VoicePill()
        self.pill.open_requested.connect(self._open_from_pill)
        self.pill.stop_requested.connect(self.voice_stop_requested.emit)
        try:
            from voxos.core.events.bus import get_event_bus
            get_event_bus().subscribe_all(self._on_event)
        except Exception:  # noqa: BLE001 — the window still works without live steps
            pass

        self.nav.select(0, animate=False)
        self.chat_page.refresh_greeting()

        # Backward-compatible attribute: older code and tests poke this.
        self.status_label = self.side_status.text

    # ── navigation ───────────────────────────────────────────────────────────

    def _on_nav(self, index: int) -> None:
        self.stack.show_page(index)
        page = PAGES[index]
        if page == "profile":
            self.profile_page.refresh()
        elif page == "routines":
            self.routines_page.refresh()
        elif page == "chat":
            self.chat_page.refresh_greeting()
            QTimer.singleShot(0, self.chat_page.input.setFocus)

    def show_page(self, name: str) -> None:
        self.nav.select(PAGES.index(name))

    def switch_mode(self, index: int) -> None:
        """0 = chat, 1 = voice (kept for VoxApp)."""
        self.show_page("voice" if index == 1 else "chat")

    # ── called by VoxApp ─────────────────────────────────────────────────────

    def update_status(self, text: str, color: str = "") -> None:  # noqa: ARG002 — colour comes from the theme
        state = status_state(text)
        self.side_status.set_status(text)
        self.logo.set_state(state)
        self.chat_page.logo.set_state(state)
        self.voice_page.set_status(text)
        # The live step card is the progress indicator; dots only until it has a step.
        self.chat_page.set_thinking(state == "thinking", dots=self._activity is None or self._activity.is_empty())
        if self._voice_active or (self.pill.isVisible() and state == "idle"):
            if state != "idle" and not self._window_in_front():
                self.pill.arrive()
            self.pill.set_status(text)

    def append_transcript(self, text: str) -> None:
        role, body = message_role(text)
        if role == "user":
            self._retire_questions(body)     # spoke an answer instead of tapping
            if self._turn is None:
                self.begin_request("voice")  # a spoken turn (typed ones began already)
        questions = self._new_questions() if role == "assistant" else None
        shown = strip_questions(body, questions) if questions else body
        if shown:
            self.chat_page.add(shown, role)
        if role in ("user", "assistant"):
            self.voice_page.show_line(role, body)
            if self.pill.isVisible():
                self.pill.show_line(role, body)
        if role == "assistant":
            self.finish_request(body)
        if questions:
            self._ask(questions)

    # ── when Vox asks ────────────────────────────────────────────────────────

    def _new_questions(self) -> list[dict] | None:
        """Questions Vox asked in the reply just received (not ones already shown)."""
        if not self.session_id:
            return None
        from voxos.core.conversation import pending

        item = pending.get(self.session_id)
        if item is None or item.asked_at == self._asked_at:
            return None
        self._asked_at = item.asked_at
        return item.questions

    def _ask(self, questions: list[dict]) -> None:
        self._retire_questions(None)
        card = self.chat_page.add_question(QuestionCard(questions))
        card.answered.connect(self._answer_question)
        voice_card = QuestionCard(questions)
        voice_card.answered.connect(self._answer_question)
        self.voice_page.show_question(voice_card)
        self._question_cards = [card, voice_card]
        self.pill.show_question(questions)

    def _answer_question(self, text: str) -> None:
        for card in self._question_cards:
            if not card.done:
                card.answered_elsewhere(text)
        self._question_cards = []
        self.chat_page.active_question = None
        self.chat_page._send_text(text)

    def _retire_questions(self, text: str | None) -> None:
        """A new message while a question was open: it answered it (or moved on)."""
        for card in self._question_cards:
            if not card.done:
                card.answered_elsewhere(text) if text else card.fold(note="Vox asked something newer.")
        self._question_cards = []
        self.chat_page.active_question = None

    # ── requests: live steps, then a record for Usage ────────────────────────

    def _typed(self, text: str) -> None:
        self._retire_questions(text)
        self.begin_request("chat")

    def begin_request(self, source: str) -> None:
        self.finish_request(None)
        self._turn = {"source": source, "started": time.time(), "tools": []}
        self._activity = None   # made when the first step arrives, so chat replies stay clean

    def _ensure_activity(self) -> ActivityCard:
        if self._activity is None or self._activity.finished:
            self._activity = self.chat_page.begin_activity()
            self.chat_page.set_thinking(False)
        return self._activity

    def finish_request(self, reply: str | None) -> None:
        turn, card = self._turn, self._activity
        self._turn, self._activity = None, None
        failed = bool(card and any(r.icon.state == "failed" for r in card._rows.values()))
        if card is not None:
            card.finish(ok=not failed)
        if turn is None or reply is None:
            return
        error = failed or reply.startswith(("I couldn", "Something went wrong", "Sorry, I"))
        try:
            get_activity_log().add(Turn(
                started=turn["started"], seconds=round(time.time() - turn["started"], 2),
                kind="error" if error else "success", source=turn["source"],
                steps=card.step_count if card else 0, tools=turn["tools"],
            ))
        except OSError:
            pass

    async def _on_event(self, event) -> None:
        """Bus events from the agents, drawn as they happen (runs on the UI loop)."""
        if self.session_id and event.session_id and event.session_id != self.session_id:
            return
        kind = getattr(event.event_type, "value", str(event.event_type))
        if kind == "approval_required":
            self._show_approval(event.payload or {})
        step = describe(kind, event.payload, event.tool)
        if step is None:
            return
        if self._turn is None:
            self.begin_request("voice" if self._voice_active else "routine")
        if kind == "tool_started" and self._turn is not None:
            self._turn["tools"].append((event.payload or {}).get("tool") or event.tool or "")
        self._ensure_activity().add_step(step)

    def _show_approval(self, payload: dict) -> None:
        try:
            from voxos.core.risk_engine.engine import get_risk_engine
            approval = get_risk_engine().get_approval(str(payload.get("id")))
            if approval is not None:
                payload = {**payload, "arguments": approval.arguments}
        except Exception:  # noqa: BLE001
            pass
        card = ApprovalCard(payload)
        card.decided.connect(self._on_decided)
        self.chat_page.add_indented(card)
        if not self._window_in_front():
            self.show_page("chat")
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _on_decided(self, approval_id: str, approved: bool) -> None:
        asyncio.ensure_future(self._decide(approval_id, approved))

    async def _decide(self, approval_id: str, approved: bool) -> None:
        from voxos.core.risk_engine.approval import get_approval_gateway
        from voxos.core.risk_engine.engine import get_risk_engine

        await get_risk_engine().process_approval_response(approval_id=approval_id, approved=approved)
        get_approval_gateway().resolve(approval_id, approved)


    # ── "What's on my screen?" ───────────────────────────────────────────────

    def _look_at_screen(self) -> None:
        asyncio.ensure_future(self._look())

    async def _look(self) -> None:
        from voxos.ui.screen_look import capture_screen, describe_screen

        self.chat_page.look_btn.setEnabled(False)
        question = "What's on my screen?"
        try:
            # Step out of the way so Vox sees your screen, not its own window.
            self.showMinimized()
            await asyncio.sleep(0.45)
            png, data_url = await capture_screen()
        finally:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        self.begin_request("screen")
        self.chat_page.add_image(png, question).opened.connect(lambda: _open_png(png))
        card = self._ensure_activity()
        card.add_step(describe("tool_started", {"step_id": "look", "tool": "vision.look"}))
        self.update_status("Vox: Thinking...")
        try:
            answer = await describe_screen(data_url, question)
            card.add_step(describe("tool_completed", {"step_id": "look", "tool": "vision.look", "success": True}))
        except Exception as e:  # noqa: BLE001
            card.add_step(describe("tool_failed", {"step_id": "look", "tool": "vision.look", "error": str(e)}))
            answer = f"I couldn't read the screen: {e}"
        self.update_status("Vox: Idle")
        self.append_transcript(answer)
        self.chat_page.look_btn.setEnabled(True)

    # ── the voice pill ───────────────────────────────────────────────────────

    def set_voice_active(self, on: bool) -> None:
        self._voice_active = on
        if on and not self._window_in_front():
            self.pill.arrive()
        elif not on:
            self.pill.set_status("Vox: Idle")

    def _window_in_front(self) -> bool:
        return self.isVisible() and not self.isMinimized() and self.isActiveWindow()

    def _open_from_pill(self) -> None:
        self.pill.leave()
        self.show_page("voice")
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def send_chat_message(self) -> None:
        """Send whatever is in the chat box (kept for callers of the old window)."""
        self.chat_page._submit()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_faded_in", False):
            self._faded_in = True
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(420)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 28)
