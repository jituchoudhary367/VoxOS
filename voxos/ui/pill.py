"""
The voice pill: a small always-on-top bubble above the taskbar while you
talk to Vox, so the hotkey or wake word never has to throw the whole window
in your face. It shows the living mark, what Vox is doing, and the last
thing heard or said. Click it to open Vox; × ends the session.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from voxos.ui.theme import PALETTE, Fonts
from voxos.ui.widgets import LogoMark, soft_shadow, status_state

WIDTH = 420
_WORDS = {"listening": "Listening…", "thinking": "Thinking…", "speaking": "Speaking…",
          "error": "Something went wrong", "idle": "Done"}


class VoicePill(QWidget):
    open_requested = Signal()
    stop_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH + 24, 96)

        self.card = QFrame(self)
        self.card.setObjectName("pill")
        self.card.setGeometry(12, 10, WIDTH, 72)
        self.card.setStyleSheet(
            f"QFrame#pill {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 36px; } QLabel { background: transparent; }"
        )
        soft_shadow(self.card, blur=30, y=8, alpha=45)
        row = QHBoxLayout(self.card)
        row.setContentsMargins(14, 8, 12, 8)
        row.setSpacing(10)
        self.mark = LogoMark(52)
        row.addWidget(self.mark)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.state = QLabel("Listening…")
        self.state.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 17px; color: {PALETTE['text']};")
        self.line = QLabel("Say something…")
        self.line.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_muted']};")
        text.addStretch(1)
        text.addWidget(self.state)
        text.addWidget(self.line)
        text.addStretch(1)
        row.addLayout(text, 1)
        close = QPushButton("×")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Stop listening")
        close.setFixedSize(34, 34)
        close.setStyleSheet(
            f"QPushButton {{ background: {PALETTE['surface_alt']}; border: none; border-radius: 17px;"
            f" font-size: 18px; color: {PALETTE['text_muted']}; padding: 0; }}"
            f"QPushButton:hover {{ background: {PALETTE['danger_soft']}; color: {PALETTE['danger']}; }}"
        )
        close.clicked.connect(self.stop_requested.emit)
        row.addWidget(close)

        self._anim = QParallelAnimationGroup(self)
        self._after = None
        self._anim.finished.connect(self._anim_done)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.leave)
        self._leaving = False

    # ── position & motion ────────────────────────────────────────────────────

    def _home(self) -> QPoint:
        screen = QApplication.primaryScreen().availableGeometry()
        return QPoint(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 18)

    def _animate(self, start: QPoint, end: QPoint, a: float, b: float, after=None) -> None:
        self._anim.stop()
        self._anim.clear()
        move = QPropertyAnimation(self, b"pos")
        move.setDuration(320)
        move.setStartValue(start)
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutBack if b > a else QEasingCurve.Type.InCubic)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(260)
        fade.setStartValue(a)
        fade.setEndValue(b)
        self._anim.addAnimation(move)
        self._anim.addAnimation(fade)
        self._after = after
        self._anim.start()

    def _anim_done(self) -> None:
        if self._after:
            self._after()

    def arrive(self) -> None:
        self._hide_timer.stop()
        if self.isVisible() and not self._leaving:
            return
        self._leaving = False
        home = self._home()
        self.setWindowOpacity(0.0)
        self.move(home + QPoint(0, 40))
        self.show()
        self._animate(home + QPoint(0, 40), home, 0.0, 1.0)

    def leave(self) -> None:
        if not self.isVisible() or self._leaving:
            return
        self._leaving = True
        self._animate(self.pos(), self.pos() + QPoint(0, 30), 1.0, 0.0, after=self._gone)

    def _gone(self) -> None:
        if self._leaving:
            self.hide()
            self._leaving = False

    # ── content ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()

    def set_status(self, text: str) -> None:
        state = status_state(text)
        self.mark.set_state(state)
        self.state.setText(_WORDS[state])
        if state == "idle":
            self._hide_timer.start(2600)   # linger so the last reply can be read
        else:
            self._hide_timer.stop()

    def show_question(self, questions: list[dict]) -> None:
        """While listening, the options are read out; here they are numbered to say back."""
        from voxos.ui.question_card import normalise

        qs = normalise(questions)
        if not qs:
            return
        q = qs[0]
        options = "  ".join(f"{n} {o['label']}" for n, o in enumerate(q["options"], 1))
        more = f"  (+{len(qs) - 1} more)" if len(qs) > 1 else ""
        self.state.setText("Your turn")
        fm = QFontMetrics(self.line.font())
        self.line.setText(fm.elidedText(f"{options}{more}", Qt.TextElideMode.ElideRight, WIDTH - 150))
        self.line.setToolTip(q["question"])
        self.line.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {PALETTE['terracotta_dark']};")

    def show_line(self, role: str, text: str) -> None:
        fm = QFontMetrics(self.line.font())
        shown = f"“{text}”" if role == "user" else text
        self.line.setText(fm.elidedText(shown, Qt.TextElideMode.ElideRight, WIDTH - 150))
        self.line.setToolTip(text)
        tone = PALETTE["text_muted"] if role == "user" else PALETTE["green_dark"]
        self.line.setStyleSheet(f"font-size: 13px; color: {tone};")
