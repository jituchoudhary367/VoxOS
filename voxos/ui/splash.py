"""
The start-up card: the mark builds itself bar by bar, the name fades in, and
a thin line tracks start-up until the main window takes over.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from voxos.ui.theme import PALETTE, Fonts
from voxos.ui.widgets import LogoMark, SlimProgress, fade_in, soft_shadow


class Splash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(480, 380)

        card = QFrame(self)
        card.setObjectName("splash_card")
        card.setGeometry(24, 20, 432, 336)
        card.setStyleSheet(
            f"QFrame#splash_card {{ background: {PALETTE['bg']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 24px; }"
        )
        soft_shadow(card, blur=40, y=10, alpha=40)

        col = QVBoxLayout(card)
        col.setContentsMargins(40, 34, 40, 30)
        col.setSpacing(4)
        self.mark = LogoMark(128, intro=True)
        col.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)

        self.name = QLabel("Vox")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 40px; color: {PALETTE['text']};")
        self.tagline = QLabel("YOUR VOICE. YOUR COMPUTER.")
        self.tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tagline.setStyleSheet(
            f"font-size: 11px; font-weight: 600; letter-spacing: 3px; color: {PALETTE['text_muted']};"
        )
        col.addWidget(self.name)
        col.addWidget(self.tagline)
        col.addStretch(1)

        self.progress = SlimProgress()
        self.status = QLabel("Waking up…")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_faint']};")
        col.addWidget(self.progress)
        col.addSpacing(6)
        col.addWidget(self.status)

        for w in (self.name, self.tagline):
            w.hide()
        QTimer.singleShot(650, self._reveal_name)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def _reveal_name(self) -> None:
        for delay, w in ((0, self.name), (120, self.tagline)):
            w.show()
            fade_in(w, 380, 8, delay=delay)

    def set_status(self, text: str, fraction: float) -> None:
        self.status.setText(text)
        self.progress.set_value(fraction)
        QApplication.processEvents()

    def finish(self, window: QWidget | None) -> None:
        """Fade out, and let `window` fade in in its place."""
        self.progress.set_value(1.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(320)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.close)
        QTimer.singleShot(250, anim.start)
        if window is not None:
            QTimer.singleShot(250, window.raise_)
