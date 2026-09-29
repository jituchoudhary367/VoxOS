"""
Things that appear in the chat besides plain messages:

- ActivityCard: the agents' steps, live, under a request; folds into a
  one-line summary ("3 steps · 2.1 s") when the answer arrives.
- ApprovalCard: a risky action waiting for Approve / Deny, with a countdown.
- ImageBubble: a screenshot you asked Vox to look at.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from voxos.ui.activity import Step, tool_words
from voxos.ui.theme import PALETTE, color
from voxos.ui.widgets import button, fade_in, label


class StepIcon(QWidget):
    """Spinner while running, a check when done, a cross when it failed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.state = "running"
        self._angle = 0.0

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def spin(self, degrees: float) -> None:
        if self.state == "running":
            self._angle = (self._angle + degrees) % 360
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(9, 9)
        if self.state == "running":
            p.setPen(QPen(color("border_strong"), 2))
            p.drawEllipse(c, 6.5, 6.5)
            pen = QPen(color("terracotta"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(2.5, 2.5, 13, 13), int(-self._angle * 16), 100 * 16)
            return
        tone = {"done": "green", "failed": "danger", "info": "amber", "stopped": "border_strong"}.get(
            self.state, "text_muted")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color(tone))
        p.drawEllipse(c, 8, 8)
        pen = QPen(QColor("white"), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if self.state == "done":
            path = QPainterPath(QPointF(5.2, 9.3))
            path.lineTo(7.8, 11.8)
            path.lineTo(12.8, 6.6)
            p.drawPath(path)
        elif self.state == "failed":
            p.drawLine(QPointF(6.2, 6.2), QPointF(11.8, 11.8))
            p.drawLine(QPointF(11.8, 6.2), QPointF(6.2, 11.8))
        else:
            p.setBrush(QColor("white"))
            p.drawEllipse(c, 1.8, 1.8)


class _StepRow(QWidget):
    def __init__(self, step: Step, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)
        self.icon = StepIcon()
        self.text = QLabel()
        self.text.setStyleSheet(f"background: transparent; font-size: 13px; color: {PALETTE['text']};")
        self.detail = QLabel()
        self.detail.setStyleSheet(f"background: transparent; font-size: 12px; color: {PALETTE['text_faint']};")
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(self.detail)
        self.started = time.perf_counter()
        self.tool = step.tool
        self.repeat = 1
        self.base_text = step.text
        self.update_step(step)

    def update_step(self, step: Step) -> None:
        self.icon.set_state(step.state)
        self.base_text = step.text
        self.text.setText(step.text)
        detail = step.detail
        if step.state == "failed" and detail:
            self.text.setToolTip(detail)
            detail = ""
        self.detail.setText(detail)


class ActivityCard(QFrame):
    """The agents' steps for one request, live."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("activity")
        self.setStyleSheet(
            f"QFrame#activity {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
            "border-radius: 14px; }"
        )
        self.setMaximumWidth(520)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.head_icon = StepIcon()
        self.title = QLabel("Working on it")
        self.title.setStyleSheet(f"background: transparent; font-size: 13px; font-weight: 600; color: {PALETTE['text']};")
        self.toggle = QPushButton("Hide steps")
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {PALETTE['text_muted']};"
            " font-size: 12px; font-weight: 600; padding: 0; }"
            f"QPushButton:hover {{ color: {PALETTE['terracotta_dark']}; }}"
        )
        self.toggle.clicked.connect(self._flip)
        head.addWidget(self.head_icon)
        head.addWidget(self.title, 1)
        head.addWidget(self.toggle)
        col.addLayout(head)
        self.rows_box = QWidget()
        self.rows_box.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 4, 0, 0)
        self.rows.setSpacing(2)
        col.addWidget(self.rows_box)
        self._rows: dict[str, _StepRow] = {}
        self._started = time.perf_counter()
        self.finished = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    @property
    def step_count(self) -> int:
        return sum(1 for r in self._rows.values() if r.icon.state in ("done", "failed"))

    def add_step(self, step: Step) -> None:
        key = step.key
        if key not in self._rows and step.tool and step.state in ("done", "failed"):
            # A finish that carries no step id: close the open row for that tool.
            key = next((k for k, r in reversed(list(self._rows.items()))
                        if r.tool == step.tool and r.icon.state == "running"), key)
        if self.finished and key not in self._rows:
            return
        row = self._rows.get(key)
        if row is None:
            row = _StepRow(step)
            self._rows[key] = row
            self.rows.addWidget(row)
            fade_in(row, 220, 6)
            return
        row.update_step(step)
        self._merge_repeat(key, row)

    def _merge_repeat(self, key: str, row: "_StepRow") -> None:
        """"Read the page" three times in a row reads better as one row ×3."""
        keys = list(self._rows)
        i = keys.index(key)
        if i == 0 or row.icon.state != "done":
            return
        prev = self._rows[keys[i - 1]]
        if prev.icon.state == "done" and prev.base_text == row.base_text:
            prev.repeat += 1
            prev.text.setText(f"{prev.base_text}  ×{prev.repeat}")
            del self._rows[key]
            row.setParent(None)
            row.deleteLater()

    def _tick(self) -> None:
        self.head_icon.spin(9)
        for row in self._rows.values():
            row.icon.spin(9)

    def _flip(self) -> None:
        showing = self.rows_box.isVisible()
        self.rows_box.setVisible(not showing)
        self.toggle.setText("Show steps" if showing else "Hide steps")
        if not showing:
            fade_in(self.rows_box, 200, 6)

    def finish(self, ok: bool = True) -> None:
        """Stop the spinners, summarise, and fold the steps away."""
        if self.finished:
            return
        self.finished = True
        self._timer.stop()
        for row in self._rows.values():
            if row.icon.state == "running":
                # Never reported back: we don't know it failed, so don't say so.
                row.icon.set_state("done" if ok else "stopped")
                row.text.setText(row.base_text.rstrip("…"))
        seconds = time.perf_counter() - self._started
        n = len(self._rows)
        self.head_icon.set_state("done" if ok else "failed")
        self.title.setText(f"{n} step{'s' if n != 1 else ''}  ·  {seconds:.1f} s")
        self.title.setStyleSheet(f"background: transparent; font-size: 12px; font-weight: 600; color: {PALETTE['text_muted']};")
        self.rows_box.hide()
        self.toggle.setText("Show steps")

    def is_empty(self) -> bool:
        return not self._rows


class ApprovalCard(QFrame):
    """A risky action paused until you say yes or no."""

    decided = Signal(str, bool)  # approval id, approved

    def __init__(self, approval: dict, parent=None):
        super().__init__(parent)
        self.approval_id = str(approval.get("id"))
        self.setObjectName("approval")
        self.setMaximumWidth(520)
        self.setStyleSheet(
            f"QFrame#approval {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border_strong']};"
            f"border-left: 4px solid {PALETTE['amber']}; border-radius: 14px; }}"
            " QLabel { background: transparent; }"
        )
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(6)
        head = QHBoxLayout()
        tool = str(approval.get("tool_name") or "")
        title = label("Approve this action?")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {PALETTE['text']};")
        risk = str(approval.get("risk_level") or "").upper()
        chip = QLabel(f"{risk.title()} risk" if risk else "Needs approval")
        chip.setStyleSheet(
            f"background: {PALETTE['amber_soft']}; color: {PALETTE['amber']}; border-radius: 9px;"
            "padding: 2px 9px; font-size: 11px; font-weight: 700;"
        )
        head.addWidget(title, 1)
        head.addWidget(chip)
        col.addLayout(head)
        if tool:
            action = label(tool_words(tool)[0])
            action.setStyleSheet(f"font-size: 14px; color: {PALETTE['text']};")
            col.addWidget(action)
        reasons = approval.get("consequences") or []
        reason = "; ".join(str(r) for r in reasons[:2]) or str(approval.get("reason") or "")
        if reason:
            col.addWidget(label(reason, "field_help", wrap=True))
        args = approval.get("arguments")
        if isinstance(args, dict) and args:
            preview = ", ".join(f"{k}: {str(v)[:60]}" for k, v in list(args.items())[:3])
            mono = label(preview, "mono", wrap=True)
            mono.setStyleSheet(
                f"font-family: 'Cascadia Mono', 'Consolas'; font-size: 12px; color: {PALETTE['text_muted']};"
                f"background: {PALETTE['surface_alt']}; border-radius: 8px; padding: 6px 8px;"
            )
            col.addWidget(mono)
        row = QHBoxLayout()
        self.approve = button("Approve", "green")
        self.deny = button("Deny", "danger")
        self.approve.clicked.connect(lambda: self._decide(True))
        self.deny.clicked.connect(lambda: self._decide(False))
        self.countdown = label("", "field_help")
        row.addWidget(self.approve)
        row.addWidget(self.deny)
        row.addStretch(1)
        row.addWidget(self.countdown)
        col.addLayout(row)
        self._expires = self._parse(approval.get("expires_at"))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    @staticmethod
    def _parse(stamp) -> float | None:
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            return None
        if when.tzinfo is None:  # the risk engine stamps in UTC
            when = when.replace(tzinfo=timezone.utc)
        return when.timestamp()

    def _tick(self) -> None:
        if self._expires is None:
            return
        left = int(self._expires - time.time())
        if left <= 0:
            self.resolve("Expired", "text_muted")
            return
        self.countdown.setText(f"Expires in {left // 60}:{left % 60:02d}" if left < 3600 else "Waiting for you")

    def _decide(self, approved: bool) -> None:
        self.decided.emit(self.approval_id, approved)
        self.resolve("Approved" if approved else "Denied", "green" if approved else "danger")

    def resolve(self, text: str, tone: str) -> None:
        self._timer.stop()
        self.approve.hide()
        self.deny.hide()
        self.countdown.setText(text)
        self.countdown.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {PALETTE[tone]};")


class ImageBubble(QWidget):
    """A screenshot on the user's side of the chat; click to open it full size."""

    opened = Signal()

    def __init__(self, png: bytes, caption: str, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._pixmap.loadFromData(png)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.thumb = _Thumb(self._pixmap)
        self.thumb.clicked.connect(self.opened.emit)
        cap = QLabel(caption)
        cap.setStyleSheet(
            f"background: {PALETTE['terracotta']}; color: white; padding: 9px 14px; border-radius: 16px;"
            "border-top-right-radius: 6px; font-size: 14px;"
        )
        col.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignRight)
        col.addWidget(cap, 0, Qt.AlignmentFlag.AlignRight)
        row.addLayout(col)


class _Thumb(QWidget):
    clicked = Signal()

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        w = 320
        h = int(w * pixmap.height() / max(1, pixmap.width())) if not pixmap.isNull() else 180
        self.setFixedSize(w, min(h, 240))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open full size")

    def mousePressEvent(self, event):
        self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.setClipPath(path)
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, scaled)
        p.setClipping(False)
        p.setPen(QPen(color("border_strong"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
