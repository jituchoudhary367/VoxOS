"""
When Vox needs your input: a question card in the chat (and on the Voice page).

One question at a time, like Claude Code and Codex ask them:
- numbered options, each a large target, with an optional description;
- "Something else…" to type your own answer;
- 1–9 on the keyboard picks an option (also from the empty chat box);
- several questions step through with "2 of 3" and a review before sending;
- "Let Vox decide" when the choice genuinely doesn't matter to you.

The answer goes back exactly like a typed reply ("HP", or "1. HP; 2. Under
₹60,000"), so voice, chat and this card all feed the same pending-question
flow. Once answered, the card folds into a short record of what you chose.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from voxos.core.conversation.pending import option_label
from voxos.ui.theme import PALETTE, Fonts, Icon
from voxos.ui.widgets import button, fade_in, label, repolish

CONFIRM_OPTIONS = ("Yes, go ahead", "No")
DECIDE = "You decide"


def normalise(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{question, options: [{label, description}], confirm}] — a bare question becomes Yes / No."""
    out = []
    for q in questions:
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        options = []
        for o in q.get("options") or []:
            lbl = option_label(o)
            if lbl:
                desc = str(o.get("description") or "").strip() if isinstance(o, dict) else ""
                options.append({"label": lbl, "description": desc})
        confirm = not options
        if confirm:
            options = [{"label": lbl, "description": ""} for lbl in CONFIRM_OPTIONS]
        out.append({"question": text, "options": options[:9], "confirm": confirm})
    return out


def compose_answer(questions: list[dict[str, Any]], answers: list[str]) -> str:
    """One question: the answer itself. Several: numbered, like Vox numbered them."""
    if len(questions) == 1:
        return answers[0]
    return "; ".join(f"{n}. {a}" for n, a in enumerate(answers, 1))


class _Option(QFrame):
    picked = Signal(int)

    def __init__(self, number: int, text: str, description: str = "", parent=None):
        super().__init__(parent)
        self.number = number
        self.setObjectName("opt")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)
        self.setProperty("hover", False)
        p = PALETTE
        self.setStyleSheet(
            f"QFrame#opt {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px; }}"
            f"QFrame#opt[hover=\"true\"] {{ border-color: {p['terracotta']}; }}"
            f"QFrame#opt[selected=\"true\"] {{ background: {p['terracotta_soft']}; border: 1px solid {p['terracotta']}; }}"
            " QLabel { background: transparent; border: none; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 14, 10)
        row.setSpacing(12)
        self.badge = QLabel(str(number))
        self.badge.setFixedSize(26, 26)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge_style(False)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        main = QLabel(text)
        main.setWordWrap(True)
        main.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {p['text']};")
        text_col.addWidget(main)
        if description:
            sub = QLabel(description)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"font-size: 12px; color: {p['text_muted']};")
            text_col.addWidget(sub)
        row.addLayout(text_col, 1)
        self.check = QLabel(Icon.CHECK)
        self.check.setStyleSheet(f"font-family: '{Fonts.icons}'; font-size: 13px; color: {p['terracotta_dark']};")
        self.check.hide()
        row.addWidget(self.check)

    def _badge_style(self, on: bool) -> None:
        p = PALETTE
        self.badge.setStyleSheet(
            f"background: {p['terracotta'] if on else p['surface_alt']}; color: {'white' if on else p['text_muted']};"
            " border-radius: 8px; font-size: 12px; font-weight: 700;"
        )

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self._badge_style(on)
        self.check.setVisible(on)
        repolish(self)

    def enterEvent(self, event):
        self.setProperty("hover", True)
        repolish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setProperty("hover", False)
        repolish(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.number)


class QuestionCard(QFrame):
    """Vox's question(s), answerable with a click, a number key or your own words."""

    answered = Signal(str)

    def __init__(self, questions: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.questions = normalise(questions)
        self.answers: list[str | None] = [None] * len(self.questions)
        self.index = 0
        self.done = False
        self._reviewing = False
        self._options: list[_Option] = []
        self.setObjectName("question")
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        # Never squeezed below its natural height (the Voice page is tight).
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        p = PALETTE
        self.setStyleSheet(
            f"QFrame#question {{ background: {p['surface']}; border: 1px solid {p['border_strong']};"
            f"border-top: 3px solid {p['terracotta']}; border-radius: 16px; }}"
            " QLabel { background: transparent; }"
        )
        col = QVBoxLayout(self)
        col.setContentsMargins(18, 14, 18, 14)
        col.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.head_icon = QLabel(Icon.SPARKLE)
        self.head_icon.setStyleSheet(f"font-family: '{Fonts.icons}'; font-size: 12px; color: {p['terracotta']};")
        self.head = QLabel("VOX NEEDS YOUR INPUT")
        self.head.setStyleSheet(f"font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {p['terracotta_dark']};")
        self.step = QLabel("")
        self.step.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {p['text_muted']};")
        head.addWidget(self.head_icon)
        head.addWidget(self.head)
        head.addStretch(1)
        head.addWidget(self.step)
        col.addLayout(head)

        self.progress = QHBoxLayout()
        self.progress.setSpacing(4)
        self._segments: list[QFrame] = []
        if len(self.questions) > 1:
            for _ in self.questions:
                seg = QFrame()
                seg.setFixedHeight(4)
                self._segments.append(seg)
                self.progress.addWidget(seg, 1)
            col.addLayout(self.progress)

        self.body = QWidget()
        self.body_col = QVBoxLayout(self.body)
        self.body_col.setContentsMargins(0, 0, 0, 0)
        self.body_col.setSpacing(8)
        col.addWidget(self.body)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.back = button("Back", "ghost")
        self.back.clicked.connect(self._back)
        self.hint = label("", "field_help")
        self.decide = button("Let Vox decide", "ghost", "Fine with anything? Vox picks.")
        self.decide.clicked.connect(lambda: self._answer(DECIDE))
        self.send = button("Send answers", "primary")
        self.send.clicked.connect(self._submit)
        foot.addWidget(self.back)
        foot.addWidget(self.hint, 1)
        foot.addWidget(self.decide)
        foot.addWidget(self.send)
        self.footer = QWidget()
        self.footer.setLayout(foot)
        col.addWidget(self.footer)
        self._render()

    # ── drawing ──────────────────────────────────────────────────────────────

    def _clear_body(self) -> None:
        while self.body_col.count():
            item = self.body_col.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._options = []

    def _render(self) -> None:
        self._clear_body()
        n = len(self.questions)
        p = PALETTE
        for i, seg in enumerate(self._segments):
            tone = p["terracotta"] if self.answers[i] is not None else (
                p["terracotta_soft"] if i == self.index and not self._reviewing else p["border"])
            seg.setStyleSheet(f"background: {tone}; border-radius: 2px; border: none;")
        if self._reviewing:
            self._render_review()
            return
        q = self.questions[self.index]
        self.step.setText(f"{self.index + 1} of {n}" if n > 1 else "")
        text = QLabel(q["question"])
        text.setWordWrap(True)
        text.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 18px; color: {p['text']};")
        self.body_col.addWidget(text)
        for k, opt in enumerate(q["options"], 1):
            row = _Option(k, opt["label"], opt["description"])
            row.picked.connect(self.pick_number)
            row.set_selected(self.answers[self.index] == opt["label"])
            self._options.append(row)
            self.body_col.addWidget(row)
        self.other = QLineEdit()
        self.other.setPlaceholderText("Something else…  type your answer and press Enter")
        chosen = self.answers[self.index]
        if chosen and chosen not in [o["label"] for o in q["options"]] and chosen != DECIDE:
            self.other.setText(chosen)
        self.other.returnPressed.connect(lambda: self._answer(self.other.text().strip()))
        self.body_col.addWidget(self.other)

        last = len(q["options"])
        self.hint.setText(f"Press 1–{last}, or type your own" if last > 1 else "Press 1, or type your own")
        self.back.setVisible(self.index > 0)
        self.decide.setVisible(not q["confirm"] and last >= 2)
        self.send.hide()
        self.footer.show()

    def _render_review(self) -> None:
        p = PALETTE
        self.step.setText("Review")
        title = QLabel("Send these answers?")
        title.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 18px; color: {p['text']};")
        self.body_col.addWidget(title)
        for i, q in enumerate(self.questions):
            row = QFrame()
            row.setObjectName("rev")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.setToolTip("Change this answer")
            row.setStyleSheet(
                f"QFrame#rev {{ background: {p['surface_alt']}; border-radius: 10px; }}"
                f"QFrame#rev:hover {{ background: {p['terracotta_soft']}; }} QLabel {{ background: transparent; }}"
            )
            line = QVBoxLayout(row)
            line.setContentsMargins(12, 8, 12, 8)
            line.setSpacing(1)
            line.addWidget(label(q["question"], "field_help", wrap=True))
            ans = QLabel(self.answers[i] or "")
            ans.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {p['text']};")
            line.addWidget(ans)
            row.mousePressEvent = lambda _e, k=i: self._goto(k)  # type: ignore[method-assign]
            self.body_col.addWidget(row)
        self.hint.setText("Click an answer to change it")
        self.back.setVisible(True)
        self.decide.hide()
        self.send.show()
        self.send.setFocus()

    # ── choosing ─────────────────────────────────────────────────────────────

    def pick_number(self, number: int) -> bool:
        """Choose option `number` (1-based) of the current question. False if there is none."""
        if self.done or self._reviewing:
            return False
        options = self.questions[self.index]["options"]
        if not 1 <= number <= len(options):
            return False
        for row in self._options:
            row.set_selected(row.number == number)
        label_text = options[number - 1]["label"]
        QTimer.singleShot(170, lambda: self._answer(label_text))  # let the selection show
        return True

    def _answer(self, text: str) -> None:
        if self.done or not text:
            return
        self.answers[self.index] = text
        if len(self.questions) == 1:
            self._submit()
        elif self.index < len(self.questions) - 1:
            self.index += 1
            self._render()
            fade_in(self.body, 200, 8)
        else:
            self._reviewing = True
            self._render()
            fade_in(self.body, 200, 8)

    def _back(self) -> None:
        if self._reviewing:
            self._reviewing = False
            self.index = len(self.questions) - 1
        elif self.index > 0:
            self.index -= 1
        self._render()
        fade_in(self.body, 200, 8)

    def _goto(self, index: int) -> None:
        self._reviewing = False
        self.index = index
        self._render()
        fade_in(self.body, 200, 8)

    def _submit(self) -> None:
        if self.done or any(a is None for a in self.answers):
            return
        text = compose_answer(self.questions, [a or "" for a in self.answers])
        self.fold(self.answers)
        self.answered.emit(text)

    def keyPressEvent(self, event):
        key = event.text()
        if key.isdigit() and self.pick_number(int(key)):
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._reviewing:
            self._submit()
            return
        super().keyPressEvent(event)

    # ── after ────────────────────────────────────────────────────────────────

    def fold(self, answers: list[str | None] | None = None, note: str = "") -> None:
        """Shrink to a record of what was chosen; nothing is clickable any more."""
        if self.done:
            return
        self.done = True
        p = PALETTE
        self._clear_body()
        self.footer.hide()
        self.step.setText("")
        for seg in self._segments:
            seg.hide()
        self.head_icon.setText(Icon.CHECK)
        self.head_icon.setStyleSheet(f"font-family: '{Fonts.icons}'; font-size: 12px; color: {p['green']};")
        self.head.setText("ANSWERED")
        self.head.setStyleSheet(f"font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {p['green_dark']};")
        self.setStyleSheet(self.styleSheet().replace(f"border-top: 3px solid {p['terracotta']}",
                                                     f"border-top: 3px solid {p['green']}"))
        if note:
            self.body_col.addWidget(label(note, "field_help", wrap=True))
        for q, a in zip(self.questions, answers or []):
            if a is None:
                continue
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(label(q["question"], "field_help", wrap=True), 1)
            ans = QLabel(a)
            ans.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {p['text']};")
            row.addWidget(ans)
            holder = QWidget()
            holder.setLayout(row)
            self.body_col.addWidget(holder)

    def answered_elsewhere(self, text: str) -> None:
        """You replied by typing or speaking instead: keep the record, retire the buttons."""
        self.fold(note=f"You answered: “{text}”")
