"""
Small charts for the Usage page, painted with QPainter.

Rules followed (dataviz guidance): one series → no legend, the title names
it; bars ≤ 24 px with a 4 px rounded data end and a square baseline; hairline
solid gridlines one step off the surface; clean round ticks; text in text
colours, never the series colour; every bar has a hover tooltip; the current
period in the accent, the rest in a lighter step of the same hue.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from voxos.ui.theme import PALETTE, Fonts, color

TERRACOTTA_LIGHT = "#E9BFAC"  # the de-emphasis step of the terracotta ramp


def nice_max(value: float) -> tuple[float, float]:
    """(axis max, tick step) with 1-2-5 steps and ~4 ticks."""
    if value <= 0:
        return 4.0, 1.0
    raw = value / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    step = max(step, 1.0) if value >= 1 else step
    return math.ceil(value / step) * step, step


class StatTile(QFrame):
    """label · value · caption — the value in the UI sans, semibold, never the serif."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self.setObjectName("stat")
        self.setStyleSheet(
            f"QFrame#stat {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 14px; } QLabel { background: transparent; }"
        )
        col = QVBoxLayout(self)
        col.setContentsMargins(18, 14, 18, 14)
        col.setSpacing(2)
        self.label = QLabel(caption)
        self.label.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_muted']};")
        self.value = QLabel("–")
        self.value.setStyleSheet(f"font-size: 28px; font-weight: 600; color: {PALETTE['text']};")
        self.note = QLabel("")
        self.note.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_faint']};")
        col.addWidget(self.label)
        col.addWidget(self.value)
        col.addWidget(self.note)

    def set(self, value: str, note: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)


class BarChart(QWidget):
    """Single-series columns with a hover tooltip."""

    def __init__(self, tooltip: Callable[[int, float], str], parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.values: list[float] = []
        self.labels: list[str] = []
        self.highlight: int | None = None
        self.empty_text = "Nothing yet"
        self._hover: int | None = None
        self._tooltip = tooltip
        self._slots: list[QRectF] = []

    def set_data(self, values: list[float], labels: list[str], highlight: int | None = None) -> None:
        self.values, self.labels, self.highlight = list(values), list(labels), highlight
        self.update()

    def _font(self, px: int, bold: bool = False) -> QFont:
        f = QFont(Fonts.body)
        f.setPixelSize(px)
        if bold:
            f.setWeight(QFont.Weight.DemiBold)
        return f

    def mouseMoveEvent(self, event):
        pos = event.position()
        hit = next((i for i, r in enumerate(self._slots) if r.contains(pos)), None)
        if hit != self._hover:
            self._hover = hit
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        left, right, top, bottom = 34, 8, 10, 26
        plot = QRectF(left, top, w - left - right, h - top - bottom)
        top_value, step = nice_max(max(self.values, default=0))

        # gridlines + ticks
        p.setFont(self._font(11))
        ticks = int(round(top_value / step))
        for k in range(ticks + 1):
            v = k * step
            y = plot.bottom() - plot.height() * v / top_value
            p.setPen(QPen(color("border"), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(color("text_faint"))
            text = f"{v:g}"
            p.drawText(QRectF(0, y - 8, left - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)

        n = len(self.values)
        self._slots = []
        if not n or not any(self.values):
            p.setPen(color("text_muted"))
            p.setFont(self._font(13))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.empty_text)
            if not n:
                return
        slot = plot.width() / n
        bar_w = min(24.0, slot * 0.62)
        for i, v in enumerate(self.values):
            x = plot.left() + slot * i + (slot - bar_w) / 2
            self._slots.append(QRectF(plot.left() + slot * i, plot.top(), slot, plot.height()))
            if i % max(1, n // 8) == 0 and i < len(self.labels):
                p.setPen(color("text_faint"))
                p.setFont(self._font(11))
                p.drawText(QRectF(plot.left() + slot * i - 10, plot.bottom() + 6, slot + 20, 16),
                           Qt.AlignmentFlag.AlignHCenter, self.labels[i])
            if v <= 0:
                continue
            bh = max(3.0, plot.height() * v / top_value)
            rect = QRectF(x, plot.bottom() - bh, bar_w, bh)
            fill = color("terracotta") if i == self.highlight else QColor(TERRACOTTA_LIGHT)
            if self._hover == i:
                fill = color("terracotta_dark")
            path = QPainterPath()
            r = min(4.0, bar_w / 2, bh)
            path.moveTo(rect.left(), rect.bottom())
            path.lineTo(rect.left(), rect.top() + r)
            path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
            path.lineTo(rect.right() - r, rect.top())
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
            path.lineTo(rect.right(), rect.bottom())
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawPath(path)

        if self._hover is not None and self._hover < n:
            text = self._tooltip(self._hover, self.values[self._hover])
            font = self._font(12, bold=True)
            fm = QFontMetrics(font)
            tw, th = fm.horizontalAdvance(text) + 20, 28
            slot_rect = self._slots[self._hover]
            tx = min(max(4.0, slot_rect.center().x() - tw / 2), w - tw - 4)
            ty = max(2.0, plot.top())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("text"))
            p.drawRoundedRect(QRectF(tx, ty, tw, th), 8, 8)
            p.setPen(color("bg"))
            p.setFont(font)
            p.drawText(QRectF(tx, ty, tw, th), Qt.AlignmentFlag.AlignCenter, text)


class Meter(QWidget):
    """Remaining quota: the fill turns amber then red as it runs low."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.fraction: float | None = None

    def set_fraction(self, fraction: float | None) -> None:
        self.fraction = None if fraction is None else max(0.0, min(1.0, fraction))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        f = self.fraction
        tone = "green" if f is None or f > 0.5 else "amber" if f > 0.2 else "danger"
        p.setBrush(color(f"{tone}_soft"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 8), 4, 4)
        if f:
            p.setBrush(color(tone))
            p.drawRoundedRect(QRectF(0, 0, max(8.0, self.width() * f), 8), 4, 4)
