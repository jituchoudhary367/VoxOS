"""
The building blocks of Vox's windows, with their motion.

Motion rules used throughout: things enter with a short fade and a small
upward slide (OutCubic, 180–280 ms), state changes glide instead of jump,
and nothing loops unless it means "something is happening right now".
"""

from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
    QRectF, QTimer, QVariantAnimation, Qt, Property, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient, QPainter, QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from voxos.ui.theme import PALETTE, Fonts, Icon, color, icon_font


# ── helpers ──────────────────────────────────────────────────────────────────

def button(text: str, kind: str | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if kind:
        btn.setProperty("kind", kind)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def icon_button(glyph: str, tooltip: str, danger: bool = False) -> QPushButton:
    return button(glyph, "icon_danger" if danger else "icon", tooltip)


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def glyph(char: str, size: int = 16, tint: str = "text_muted") -> QLabel:
    lbl = QLabel(char)
    lbl.setFont(icon_font(size))
    lbl.setStyleSheet(
        f"color: {PALETTE[tint]}; background: transparent;"
        f"font-family: '{Fonts.icons}'; font-size: {size}px;"
    )
    return lbl


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def soft_shadow(widget: QWidget, blur: int = 28, y: int = 6, alpha: int = 22) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(80, 50, 30, alpha))
    widget.setGraphicsEffect(effect)


def _rise(widget: QWidget, rise: int, duration: int) -> None:
    """Slide a widget up by `rise` px without fighting its parent's layout.

    Moving `pos` directly only works where no layout re-places the widget
    (pages of a QStackedWidget); inside a box layout the next relayout snaps
    it back mid-flight and rows overlap. There the top margin animates instead.
    """
    if isinstance(widget.parentWidget(), QStackedWidget):
        end = widget.pos()
        slide = QPropertyAnimation(widget, b"pos", widget)
        slide.setDuration(duration)
        slide.setStartValue(end + QPoint(0, rise))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return
    layout = widget.layout()
    if layout is None:
        return
    m = getattr(widget, "_vox_margins", None) or layout.contentsMargins()
    widget._vox_margins = m
    running = getattr(widget, "_vox_rise", None)
    if running is not None:
        running.stop()
    anim = QVariantAnimation(widget)
    widget._vox_rise = anim
    anim.setDuration(duration)
    anim.setStartValue(m.top() + rise)
    anim.setEndValue(m.top())
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda v: layout.setContentsMargins(m.left(), int(v), m.right(), m.bottom()))
    anim.start()


def fade_in(widget: QWidget, duration: int = 260, rise: int = 10, delay: int = 0) -> None:
    """Fade a widget in while it rises a few pixels. Leaves no effect behind.

    Safe to call again while a fade is running (status labels do): the old
    animation is stopped before its opacity effect is replaced, otherwise Qt
    warns "Changing state of an animation without target".
    """
    previous = getattr(widget, "_vox_fade", None)
    if previous is not None:
        previous.stop()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.Type.OutCubic)
    widget._vox_fade = fade

    def start() -> None:
        if widget.graphicsEffect() is not effect:  # superseded before it began
            return
        if rise:
            _rise(widget, rise, duration)
        fade.start()

    def done() -> None:
        # Drop the effect afterwards: an opacity effect renders its widget
        # off-screen, which blurs text and costs a repaint per frame.
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        if getattr(widget, "_vox_fade", None) is fade:
            widget._vox_fade = None

    fade.finished.connect(done)
    # Bound to the widget: if it is deleted first (a merged step row), Qt
    # cancels the start instead of running it on a dead object.
    QTimer.singleShot(delay, widget, start)


class Card(QFrame):
    """A white rounded surface with an optional title row."""

    def __init__(self, title: str = "", subtitle: str = "", glyph_char: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 22, 24, 22)
        self.body.setSpacing(14)
        self.header = QHBoxLayout()
        self.header.setSpacing(12)
        if title:
            if glyph_char:
                badge = QLabel(glyph_char)
                badge.setFont(icon_font(16))
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge.setFixedSize(36, 36)
                badge.setStyleSheet(
                    f"background: {PALETTE['terracotta_soft']}; color: {PALETTE['terracotta']};"
                    f"border-radius: 10px; font-family: '{Fonts.icons}'; font-size: 16px;"
                )
                self.header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(title, "card_title"))
            if subtitle:
                text.addWidget(label(subtitle, "card_sub", wrap=True))
            self.header.addLayout(text, 1)
            self.body.addLayout(self.header)

    def add_action(self, widget: QWidget) -> None:
        """Put a widget at the right end of the title row."""
        self.header.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, "page_title"))
        text.addWidget(label(subtitle, "page_sub", wrap=True))
        layout.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)


# ── navigation ───────────────────────────────────────────────────────────────

class NavButton(QAbstractButton):
    """A sidebar tab: glyph + label, painted so hover can fade instead of snap."""

    def __init__(self, glyph_char: str, text: str, parent=None):
        super().__init__(parent)
        self.glyph_char = glyph_char
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = 0.0
        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(160)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._badge = 0

    def set_badge(self, count: int) -> None:
        self._badge = count
        self.update()

    def _get_hover(self) -> float:
        return self._hover

    def _set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = Property(float, _get_hover, _set_hover)

    def _animate_hover(self, target: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.isChecked() and self._hover > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("border", int(150 * self._hover)))
            painter.drawRoundedRect(rect, 10, 10)

        tint = color("terracotta_dark") if self.isChecked() else color("text_muted")
        if not self.isChecked() and self._hover > 0:
            tint = color("text")
        painter.setPen(tint)
        painter.setFont(icon_font(16))
        painter.drawText(QRectF(16, 0, 24, self.height()), Qt.AlignmentFlag.AlignVCenter, self.glyph_char)

        font = QFont(Fonts.body)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(color("text") if self.isChecked() else tint)
        painter.drawText(QRectF(50, 0, self.width() - 60, self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self.text())

        if self._badge:
            text = str(self._badge) if self._badge < 100 else "99+"
            w = max(20, 8 + 7 * len(text))
            badge = QRectF(self.width() - w - 12, (self.height() - 20) / 2, w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("green"))
            painter.drawRoundedRect(badge, 10, 10)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)


class SideNav(QWidget):
    """Vertical tabs with a pill that glides to the selected one."""

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons: list[NavButton] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.pill = QFrame(self)
        self.pill.setStyleSheet(
            f"background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 10px;"
        )
        self.accent = QFrame(self.pill)
        self.accent.setStyleSheet(f"background: {PALETTE['terracotta']}; border-radius: 2px; border: none;")
        self.pill.lower()
        self._pill_anim = QPropertyAnimation(self.pill, b"geometry", self)
        self._pill_anim.setDuration(280)
        self._pill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.current = -1

    def add(self, glyph_char: str, text: str) -> NavButton:
        btn = NavButton(glyph_char, text, self)
        index = len(self.buttons)
        btn.clicked.connect(lambda: self.select(index))
        self.buttons.append(btn)
        self._layout.addWidget(btn)
        return btn

    def add_section(self, text: str) -> None:
        lbl = label(text.upper(), "nav_section")
        lbl.setContentsMargins(16, 14, 0, 4)
        self._layout.addWidget(lbl)

    def select(self, index: int, animate: bool = True) -> None:
        for i, btn in enumerate(self.buttons):
            btn.setChecked(i == index)
        target = self.buttons[index].geometry()
        self.accent.setGeometry(0, 12, 3, max(0, target.height() - 24))
        if animate and self.current >= 0 and self.isVisible():
            self._pill_anim.stop()
            self._pill_anim.setStartValue(self.pill.geometry())
            self._pill_anim.setEndValue(target)
            self._pill_anim.start()
        else:
            self.pill.setGeometry(target)
        changed = index != self.current
        self.current = index
        if changed:
            self.changed.emit(index)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current >= 0:
            QTimer.singleShot(0, lambda: self.pill.setGeometry(self.buttons[self.current].geometry()))

    def showEvent(self, event):
        super().showEvent(event)
        if self.current >= 0:
            QTimer.singleShot(0, lambda: self.pill.setGeometry(self.buttons[self.current].geometry()))


class FadeStack(QStackedWidget):
    """A stacked widget whose pages fade and rise in when switched to."""

    def show_page(self, index: int) -> None:
        if index == self.currentIndex():
            return
        self.setCurrentIndex(index)
        fade_in(self.currentWidget(), duration=240, rise=14)


# ── status & feedback ────────────────────────────────────────────────────────

def status_tone(text: str) -> str:
    """Map a free-text status ("Vox: Listening...") to a palette tone."""
    t = text.lower()
    if "error" in t or "fail" in t:
        return "danger"
    if "unavailable" in t:
        return "amber"
    if "listening" in t:
        return "green"
    if "speaking" in t:
        return "green_dark"
    if "thinking" in t:
        return "terracotta"
    return "text_muted"


def status_state(text: str) -> str:
    t = text.lower()
    for state in ("error", "listening", "speaking", "thinking"):
        if state in t or (state == "error" and "fail" in t):
            return state
    return "idle"


class StatusPill(QFrame):
    """Rounded status chip with a dot that breathes while Vox is busy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 14, 0)
        layout.setSpacing(8)
        self.dot = _PulseDot(self)
        self.text = QLabel("Idle")
        self.text.setStyleSheet("background: transparent; font-weight: 600; font-size: 13px;")
        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        self.set_status("Idle")

    def set_status(self, text: str) -> None:
        clean = text.split(":", 1)[-1].strip().rstrip(".") or text
        tone = status_tone(text)
        self.text.setText(clean)
        self.text.setStyleSheet(
            f"background: transparent; font-weight: 600; font-size: 13px; color: {PALETTE[tone]};"
        )
        self.setStyleSheet(
            f"StatusPill {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 17px; }"
        )
        self.dot.set(PALETTE[tone], busy=status_state(text) != "idle" and tone != "danger")


class _PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = QColor(PALETTE["text_muted"])
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set(self, hex_color: str, busy: bool) -> None:
        self._color = QColor(hex_color)
        if busy and not self._timer.isActive():
            self._timer.start(33)
        elif not busy:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.06) % 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(7, 7)
        if self._timer.isActive():
            halo = QColor(self._color)
            halo.setAlphaF(0.35 * (1 - self._phase))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            r = 3.5 + 3.5 * self._phase
            p.drawEllipse(c, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(c, 3.5, 3.5)


class Toast(QFrame):
    """A small message that slides up from the bottom of a window, then leaves."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setObjectName("toast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 18, 12)
        layout.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFont(icon_font(14))
        self.msg = QLabel()
        self.msg.setStyleSheet("background: transparent; color: white; font-weight: 500;")
        layout.addWidget(self.icon)
        layout.addWidget(self.msg)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._leave)
        self._anim = QParallelAnimationGroup(self)
        self._hide_after = False
        self._anim.finished.connect(lambda: self._hide_after and self.hide())
        self.hide()

    def show_message(self, text: str, tone: str = "green", ms: int = 3200) -> None:
        bg = {"green": PALETTE["green"], "danger": PALETTE["danger"],
              "terracotta": PALETTE["terracotta"], "amber": PALETTE["amber"]}.get(tone, PALETTE["text"])
        self.setStyleSheet(f"QFrame#toast {{ background: {bg}; border-radius: 12px; }}")
        self.icon.setText(Icon.CHECK if tone == "green" else Icon.WARNING if tone in ("danger", "amber") else Icon.SPARKLE)
        self.icon.setStyleSheet(f"background: transparent; color: white; font-family: '{Fonts.icons}';")
        self.msg.setText(text)
        self.adjustSize()
        x = (self.host.width() - self.width()) // 2
        end = QPoint(x, self.host.height() - self.height() - 28)
        self._run(end + QPoint(0, 24), end, 0.0, 1.0)
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _leave(self) -> None:
        self._run(self.pos(), self.pos() + QPoint(0, 16), 1.0, 0.0, hide_after=True)

    def _run(self, start: QPoint, end: QPoint, a: float, b: float, hide_after: bool = False) -> None:
        self._anim.stop()
        self._anim.clear()
        move = QPropertyAnimation(self, b"pos")
        move.setDuration(260)
        move.setStartValue(start)
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(self._opacity, b"opacity")
        fade.setDuration(260)
        fade.setStartValue(a)
        fade.setEndValue(b)
        self._anim.addAnimation(move)
        self._anim.addAnimation(fade)
        self._hide_after = hide_after
        self._anim.start()


# ── inputs ───────────────────────────────────────────────────────────────────

class Toggle(QAbstractButton):
    """An on/off switch whose knob slides."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self.toggled.connect(self._glide)

    def _get(self) -> float:
        return self._pos

    def _set(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get, _set)

    def _glide(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = color("border_strong"), color("green")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        p.setBrush(QColor("white"))
        x = 3 + self._pos * 20
        p.drawEllipse(QRectF(x, 3, 20, 20))


class Segmented(QFrame):
    """A row of mutually exclusive options; the selection pill glides between them."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setStyleSheet(
            f"QFrame#segmented {{ background: {PALETTE['surface_alt']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 12px; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        self.pill = QFrame(self)
        self.pill.setStyleSheet(
            f"background: {PALETTE['surface']}; border: 1px solid {PALETTE['border_strong']}; border-radius: 9px;"
        )
        self._anim = QPropertyAnimation(self.pill, b"geometry", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.buttons: dict[str, QPushButton] = {}
        for value, text in options:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, v=value: self._pick(v))
            self.buttons[value] = btn
            row.addWidget(btn)
        self._value = options[0][0]
        self._restyle()

    def _restyle(self) -> None:
        for value, btn in self.buttons.items():
            on = value == self._value
            btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; padding: 8px 18px; border-radius: 9px;"
                f" font-weight: 600; color: {PALETTE['terracotta_dark'] if on else PALETTE['text_muted']}; }}"
                f"QPushButton:hover {{ color: {PALETTE['terracotta_dark'] if on else PALETTE['text']}; }}"
            )

    def _place_pill(self, animate: bool) -> None:
        target = self.buttons[self._value].geometry()
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self.pill.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.pill.setGeometry(target)
        self.pill.lower()

    def _pick(self, value: str) -> None:
        changed = value != self._value
        self._value = value
        self._restyle()
        self._place_pill(animate=True)
        if changed:
            self.changed.emit(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if value in self.buttons:
            self._value = value
            self._restyle()
            self._place_pill(animate=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self._place_pill(animate=False))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._place_pill(animate=False))


class SlimProgress(QWidget):
    """A 6 px progress track; the fill glides to each new value."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._value = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set)

    def _set(self, v) -> None:
        self._value = float(v)
        self.update()

    def set_value(self, fraction: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(max(0.0, min(1.0, fraction)))
        self._anim.start()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color("border"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._value > 0:
            grad = QLinearGradient(0, 0, self.width(), 0)
            grad.setColorAt(0, color("terracotta"))
            grad.setColorAt(1, color("green"))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self._value), 6), 3, 3)


class LogoMark(QWidget):
    """
    The Vox mark, alive. Idle it is still; listening, the dot glows; thinking,
    the dot bobs; speaking, the bars dance like a voice meter. With `intro`
    the bars rise into place one by one and the dot drops in.
    """

    _ENERGY = {"idle": 0.0, "error": 0.0, "listening": 0.25, "thinking": 0.2, "speaking": 1.0}

    def __init__(self, size: int = 40, intro: bool = False, parent=None):
        super().__init__(parent)
        from voxos.ui import brand

        self._brand = brand
        self._bars = brand.bars(7)
        self._dot = brand.dot(7)
        self.setFixedSize(size, size)
        self.state = "idle"
        self._t = 0.0
        self._energy = 0.0
        self._intro_t = 0.0 if intro else 10.0
        # Time comes from the clock, not from counting frames: a busy UI drops
        # frames, and the intro must still finish on time.
        self._last = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if intro:
            self._timer.start(16)

    def replay_intro(self) -> None:
        self._intro_t = 0.0
        self._last = time.perf_counter()
        self._timer.start(16)

    def set_state(self, state: str) -> None:
        self.state = state if state in self._ENERGY else "idle"
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start(16)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.1, now - self._last)
        self._last = now
        self._t += dt
        self._intro_t += dt
        target = self._ENERGY[self.state]
        self._energy += (target - self._energy) * min(1.0, dt * 5)
        still = self.state in ("idle", "error") and self._energy < 0.005 and self._intro_t > 1.4
        if still:
            self._energy = 0.0
            self._timer.stop()
        self.update()

    @staticmethod
    def _out_back(p: float) -> float:
        p = max(0.0, min(1.0, p))
        c1, c3 = 1.70158, 2.70158
        return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = float(min(self.width(), self.height()))
        ox, oy = (self.width() - size) / 2, (self.height() - size) / 2
        scale = 1.3
        lengths = self._brand.animated_lengths(self._t, self._energy)
        p.setPen(Qt.PenStyle.NoPen)
        for i, bar in enumerate(self._bars):
            grow = self._out_back((self._intro_t - i * 0.07) / 0.5)
            if grow <= 0:
                continue
            x, y, w, h = self._brand.bar_rect(bar, size, scale, lengths[i] * grow)
            grad = QLinearGradient(0, oy + y, 0, oy + y + h)
            grad.setColorAt(0, QColor("#D2774F"))
            grad.setColorAt(1, QColor("#AE4E2E"))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(ox + x, oy + y, w, h), w / 2, w / 2)

        cx, cy, r = self._dot
        appear = self._out_back((self._intro_t - 0.55) / 0.45)
        if appear <= 0:
            return
        bob = 0.012 * math.sin(self._t * 5) if self.state == "thinking" else 0.0
        dx = ox + (0.5 + (cx - 0.5) * scale) * size
        dy = oy + (0.5 + (cy - 0.5 + bob) * scale) * size - (1 - min(1.0, appear)) * size * 0.12
        dr = r * size * scale * appear
        if self.state == "listening":
            halo = color("green")
            halo.setAlphaF(0.18 + 0.12 * math.sin(self._t * 4))
            p.setBrush(halo)
            p.drawEllipse(QPointF(dx, dy), dr * 1.9, dr * 1.9)
        grad = QRadialGradient(QPointF(dx - dr * 0.25, dy - dr * 0.3), dr * 1.5)
        grad.setColorAt(0, QColor("#6A957A"))
        grad.setColorAt(1, color("green"))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(dx, dy), dr, dr)


class Aurora(QWidget):
    """Slow, soft light behind content: terracotta and sage glows drifting apart and back."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._t = random.uniform(0, 100)
        self._running = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_running(self, on: bool) -> None:
        """Paused, the glow stays where it is and costs nothing."""
        self._running = on
        if on and self.isVisible():
            self._timer.start(40)
        elif not on:
            self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self._running:
            self._timer.start(40)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        self._t += 0.04
        self.update()

    _SCALE = 6  # soft light has no detail: draw it small, scale it up

    def paintEvent(self, event):
        w, h = max(1, self.width() // self._SCALE), max(1, self.height() // self._SCALE)
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        q = QPainter(image)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(Qt.PenStyle.NoPen)
        t = self._t
        blobs = (
            ("terracotta", 30, 0.35 + 0.12 * math.sin(t * 0.21), 0.45 + 0.10 * math.cos(t * 0.17), 0.42),
            ("sage", 34, 0.65 + 0.12 * math.cos(t * 0.19), 0.55 + 0.10 * math.sin(t * 0.23), 0.38),
            ("amber_light", 26, 0.50 + 0.18 * math.sin(t * 0.13), 0.30 + 0.08 * math.sin(t * 0.29), 0.30),
        )
        for name, alpha, fx, fy, fr in blobs:
            centre, radius = QPointF(w * fx, h * fy), max(w, h) * fr
            grad = QRadialGradient(centre, radius)
            grad.setColorAt(0, color(name, alpha))
            grad.setColorAt(1, color(name, 0))
            q.setBrush(QBrush(grad))
            q.drawEllipse(centre, radius, radius)
        q.end()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.rect(), image)


class SecretField(QWidget):
    """A password-style input with show/hide and copy."""

    edited = Signal()

    def __init__(self, value: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.input = QLineEdit(value)
        self.input.setPlaceholderText(placeholder)
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.textEdited.connect(lambda _: self.edited.emit())
        self.eye = icon_button(Icon.EYE, "Show")
        self.eye.clicked.connect(self._toggle)
        self.copy = icon_button(Icon.COPY, "Copy")
        self.copy.clicked.connect(self._copy)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.eye)
        layout.addWidget(self.copy)

    def _toggle(self) -> None:
        hidden = self.input.echoMode() == QLineEdit.EchoMode.Password
        self.input.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.eye.setText(Icon.EYE_OFF if hidden else Icon.EYE)
        self.eye.setToolTip("Hide" if hidden else "Show")

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.input.text())
        self.copy.setText(Icon.CHECK)
        QTimer.singleShot(1200, lambda: self.copy.setText(Icon.COPY))

    def text(self) -> str:
        return self.input.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 — Qt naming
        self.input.setText(value)


# ── chat ─────────────────────────────────────────────────────────────────────

class ChatBubble(QWidget):
    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(560)
        # A word-wrapped QLabel wraps at its (small) size hint; give it the
        # width the text actually needs, up to the cap, so short replies stay on one line.
        font = QFont(Fonts.body)
        font.setPixelSize(14)
        need = QFontMetrics(font).horizontalAdvance(text) + 48
        bubble.setMinimumWidth(min(need, 440))
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        p = PALETTE
        if role == "user":
            bubble.setStyleSheet(
                f"background: {p['terracotta']}; color: white; padding: 11px 15px;"
                "border-radius: 18px; border-bottom-right-radius: 6px; font-size: 14px;"
            )
            row.addStretch(1)
            row.addWidget(bubble)
        elif role == "system":
            bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bubble.setStyleSheet(
                f"background: {p['surface_alt']}; color: {p['text_muted']}; padding: 6px 12px;"
                "border-radius: 12px; font-size: 12px;"
            )
            row.addStretch(1)
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            avatar = QLabel("V")
            avatar.setFixedSize(30, 30)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(
                f"background: {p['green']}; color: white; border-radius: 15px;"
                f"font-family: '{Fonts.display}'; font-size: 15px; font-weight: 600;"
            )
            bubble.setStyleSheet(
                f"background: {p['surface']}; color: {p['text']}; padding: 11px 15px;"
                f"border: 1px solid {p['border']}; border-radius: 18px;"
                "border-bottom-left-radius: 6px; font-size: 14px;"
            )
            row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignBottom)
            row.addWidget(bubble)
            row.addStretch(1)


class TypingIndicator(QWidget):
    """Three dots that hop in turn while Vox works on a reply."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(84, 40)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start(30)
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._t += 0.12
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(color("border"), 1))
        p.setBrush(color("surface"))
        p.drawRoundedRect(QRectF(0.5, 0.5, 83, 39), 18, 18)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            lift = max(0.0, math.sin(self._t - i * 0.7)) * 5
            c = color("terracotta")
            c.setAlphaF(0.45 + 0.55 * (lift / 5))
            p.setBrush(c)
            p.drawEllipse(QPointF(26 + i * 16, 21 - lift), 4, 4)


# ── voice ────────────────────────────────────────────────────────────────────

_ORB_TONES = {
    "idle": ("sage", "green"),
    "listening": ("sage", "green"),
    "thinking": ("terracotta_soft", "terracotta"),
    "speaking": ("sage", "green_dark"),
    "error": ("danger_soft", "danger"),
}


class VoiceOrb(QWidget):
    """
    The voice page centrepiece. Idle: a slow breath. Listening: green ripples
    leave the orb. Thinking: a terracotta arc orbits. Speaking: a ring of bars
    dances. Colour changes cross-fade instead of snapping.
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # 340 px when there's room; gives way (to a question card) down to 200.
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state = "idle"
        self._t = 0.0
        self._ripples: list[float] = []
        self._next_ripple = 0.0
        self._bars = [0.0] * 48
        self._inner = color("sage")
        self._outer = color("green")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # The orb repaints at 60 fps; run that only while it is on screen.
    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start(16)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def sizeHint(self):  # noqa: N802 — Qt override
        from PySide6.QtCore import QSize
        return QSize(340, 340)

    def set_state(self, state: str) -> None:
        self.state = state if state in _ORB_TONES else "idle"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    @staticmethod
    def _mix(a: QColor, b: QColor, k: float) -> QColor:
        return QColor(
            int(a.red() + (b.red() - a.red()) * k),
            int(a.green() + (b.green() - a.green()) * k),
            int(a.blue() + (b.blue() - a.blue()) * k),
        )

    def _tick(self) -> None:
        dt = 0.016
        self._t += dt
        inner, outer = _ORB_TONES[self.state]
        self._inner = self._mix(self._inner, color(inner), 0.08)
        self._outer = self._mix(self._outer, color(outer), 0.08)

        if self.state == "listening" and self._t >= self._next_ripple:
            self._ripples.append(0.0)
            self._next_ripple = self._t + 0.7
        self._ripples = [r + dt / 2.2 for r in self._ripples if r < 1.0]

        for i in range(len(self._bars)):
            target = 0.0
            if self.state == "speaking":
                target = 0.25 + 0.75 * abs(math.sin(self._t * 6 + i * 0.55)) * random.uniform(0.6, 1.0)
            self._bars[i] += (target - self._bars[i]) * 0.25
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height()) * 0.2

        breath = 1.0 + 0.035 * math.sin(self._t * 1.6)
        if self.state == "listening":
            breath = 1.0 + 0.06 * math.sin(self._t * 4)
        radius = base * breath

        # soft halo
        halo = QRadialGradient(c, radius * 2.4)
        edge = QColor(self._outer)
        edge.setAlpha(46)
        halo.setColorAt(0.0, edge)
        edge.setAlpha(0)
        halo.setColorAt(1.0, edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(halo))
        p.drawEllipse(c, radius * 2.4, radius * 2.4)

        # listening ripples
        for r in self._ripples:
            ring = QColor(self._outer)
            ring.setAlphaF(0.55 * (1 - r))
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = radius * (1.05 + 1.2 * r)
            p.drawEllipse(c, rr, rr)

        # speaking bars
        if max(self._bars) > 0.02:
            pen = QPen(self._outer, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            n = len(self._bars)
            for i, h in enumerate(self._bars):
                a = 2 * math.pi * i / n - math.pi / 2
                r0 = radius * 1.18
                r1 = r0 + radius * 0.55 * h
                p.drawLine(QPointF(c.x() + r0 * math.cos(a), c.y() + r0 * math.sin(a)),
                           QPointF(c.x() + r1 * math.cos(a), c.y() + r1 * math.sin(a)))

        # thinking arc
        if self.state == "thinking":
            pen = QPen(color("terracotta"), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = radius * 1.25
            start = int(-self._t * 360 * 16) % (360 * 16)
            span = int((90 + 60 * math.sin(self._t * 3)) * 16)
            p.drawArc(QRectF(c.x() - rr, c.y() - rr, rr * 2, rr * 2), start, span)

        # the orb
        body = QRadialGradient(QPointF(c.x() - radius * 0.35, c.y() - radius * 0.4), radius * 1.6)
        body.setColorAt(0.0, self._mix(self._inner, QColor("white"), 0.55))
        body.setColorAt(0.55, self._outer)
        body.setColorAt(1.0, self._mix(self._outer, QColor("black"), 0.25))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(body))
        p.drawEllipse(c, radius, radius)

        # mic glyph
        p.setPen(QColor("white"))
        p.setFont(icon_font(int(radius * 0.62)))
        p.drawText(QRectF(c.x() - radius, c.y() - radius, radius * 2, radius * 2),
                   Qt.AlignmentFlag.AlignCenter, Icon.MIC)
