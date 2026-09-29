"""
Routines page: things Vox does for you on a schedule.

Each routine is a sentence Vox runs exactly as if you had typed it, at a
time on chosen days. The scheduler itself lives in VoxApp (it needs the
agents); this page edits the list and can run one right away.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTimeEdit,
    QVBoxLayout, QWidget,
)

from voxos.ui.routines import DAY_NAMES, EVERY_DAY, TEMPLATES, WEEKDAYS, Routine, get_routines
from voxos.ui.theme import PALETTE, Fonts, Icon
from voxos.ui.widgets import Card, PageHeader, Toggle, button, fade_in, glyph, icon_button, label


class _DayChips(QWidget):
    def __init__(self, days: list[int], parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.buttons: list[QPushButton] = []
        for i, name in enumerate(DAY_NAMES):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i in days)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(46, 34)
            b.setStyleSheet(
                f"QPushButton {{ background: {PALETTE['surface']}; border: 1px solid {PALETTE['border']};"
                f" border-radius: 10px; font-weight: 600; color: {PALETTE['text_muted']}; padding: 0; }}"
                f"QPushButton:checked {{ background: {PALETTE['terracotta']}; border-color: {PALETTE['terracotta']};"
                " color: white; }"
            )
            self.buttons.append(b)
            row.addWidget(b)
        row.addStretch(1)

    def days(self) -> list[int]:
        return [i for i, b in enumerate(self.buttons) if b.isChecked()]

    def set_days(self, days) -> None:
        for i, b in enumerate(self.buttons):
            b.setChecked(i in days)


class RoutineDialog(QDialog):
    def __init__(self, routine: Routine | None = None, parent=None):
        super().__init__(parent)
        self.routine = routine
        self.setWindowTitle("Routine")
        self.setMinimumWidth(500)
        self.setStyleSheet(f"QDialog {{ background: {PALETTE['bg']}; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(8)
        title = QLabel("Edit routine" if routine else "New routine")
        title.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 24px;")
        col.addWidget(title)
        col.addWidget(label("Vox will do this at the time you pick, as if you had asked it yourself.",
                            "card_sub", wrap=True))
        col.addSpacing(6)
        self.name = QLineEdit(routine.name if routine else "")
        self.name.setPlaceholderText("Morning briefing")
        self.command = QLineEdit(routine.command if routine else "")
        self.command.setPlaceholderText("What's the top tech news today?")
        self.time = QTimeEdit(QTime.fromString(routine.time if routine else "09:00", "HH:mm"))
        self.time.setDisplayFormat("hh:mm AP")
        self.time.setFixedWidth(140)
        self.days = _DayChips(routine.days if routine else list(WEEKDAYS))
        for caption, w in (("Name", self.name), ("What should Vox do?", self.command), ("At", self.time)):
            col.addWidget(label(caption, "field_label"))
            col.addWidget(w)
        col.addWidget(label("On", "field_label"))
        col.addWidget(self.days)
        presets = QHBoxLayout()
        for text, days in (("Weekdays", WEEKDAYS), ("Every day", EVERY_DAY), ("Weekends", (5, 6))):
            b = button(text, "ghost")
            b.clicked.connect(lambda _=False, d=days: self.days.set_days(d))
            presets.addWidget(b)
        presets.addStretch(1)
        col.addLayout(presets)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {PALETTE['danger']};")
        self.error.hide()
        col.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        save = button("Save routine", "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        col.addLayout(buttons)

    def prefill(self, name: str, command: str, time: str, days) -> None:
        self.name.setText(name)
        self.command.setText(command)
        self.time.setTime(QTime.fromString(time, "HH:mm"))
        self.days.set_days(days)

    def _save(self) -> None:
        name, command, days = self.name.text().strip(), self.command.text().strip(), self.days.days()
        problem = ("Give it a name." if not name else "Tell Vox what to do." if not command
                   else "Pick at least one day." if not days else "")
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        when = self.time.time().toString("HH:mm")
        store = get_routines()
        if self.routine:
            self.routine.name, self.routine.command, self.routine.time, self.routine.days = name, command, when, days
            store.save()
        else:
            self.routine = store.add(Routine(name=name, command=command, time=when, days=days))
        self.accept()


class RoutinesPage(QWidget):
    run_requested = Signal(object)   # Routine
    notify = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("page_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("page")
        col = QVBoxLayout(content)
        col.setContentsMargins(44, 36, 44, 36)
        col.setSpacing(20)
        header = PageHeader("Routines", "Things Vox does for you on a schedule, while it runs in the tray.")
        new = button("+  New routine", "primary")
        new.clicked.connect(lambda: self._edit(None))
        header.actions.addWidget(new)
        col.addWidget(header)

        ideas = Card("Start from an idea", "", Icon.SPARKLE)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        for name, command, time, days in TEMPLATES:
            chip = button(name)
            chip.setObjectName("chip")
            chip.setToolTip(command)
            chip.clicked.connect(lambda _=False, t=(name, command, time, days): self._edit(None, t))
            chips.addWidget(chip)
        chips.addStretch(1)
        ideas.body.addLayout(chips)
        col.addWidget(ideas)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(12)
        col.addLayout(self.list_box)
        col.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.refresh()

    def refresh(self) -> None:
        while self.list_box.count():
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        items = get_routines().items
        if not items:
            empty = label("No routines yet. Pick an idea above, or make your own.", "muted", wrap=True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {PALETTE['text_faint']}; padding: 28px;"
                                f" border: 1px dashed {PALETTE['border_strong']}; border-radius: 14px;")
            self.list_box.addWidget(empty)
        now = datetime.now()
        for i, r in enumerate(items):
            card = self._card(r, now)
            self.list_box.addWidget(card)
            fade_in(card, 220, 6, delay=i * 40)

    def _card(self, r: Routine, now: datetime) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 16, 16, 16)
        row.setSpacing(14)
        badge = glyph(Icon.CLOCK, 16, "terracotta" if r.enabled else "text_faint")
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(badge.styleSheet() + f"background: {PALETTE['terracotta_soft'] if r.enabled else PALETTE['surface_alt']};"
                            " border-radius: 10px;")
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        name = label(r.name, "card_title")
        text.addWidget(name)
        text.addWidget(label(f"“{r.command}”", wrap=True))
        nxt = r.next_run(now)
        when = r.schedule_text() + (f"  ·  next {nxt.strftime('%a %I:%M %p').replace(' 0', ' ')}" if nxt else "")
        if r.last_run:
            when += f"  ·  last ran {datetime.fromisoformat(r.last_run).strftime('%d %b, %I:%M %p')}"
        text.addWidget(label(when, "field_help", wrap=True))
        row.addLayout(text, 1)
        run = button("Run now", "ghost")
        run.clicked.connect(lambda: self.run_requested.emit(r))
        edit = icon_button(Icon.SETTINGS, "Edit")
        edit.clicked.connect(lambda: self._edit(r))
        remove = icon_button(Icon.DELETE, "Delete", danger=True)
        remove.clicked.connect(lambda: self._remove(r))
        toggle = Toggle(r.enabled)
        toggle.setToolTip("On / off")
        toggle.toggled.connect(lambda on: self._enable(r, on))
        for w in (run, edit, remove, toggle):
            row.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _edit(self, routine: Routine | None, template=None) -> None:
        dialog = RoutineDialog(routine, self)
        if template:
            dialog.prefill(*template)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.notify.emit(f"“{dialog.routine.name}” saved: {dialog.routine.schedule_text().lower()}.", "green")

    def _remove(self, r: Routine) -> None:
        get_routines().remove(r.id)
        self.refresh()
        self.notify.emit(f"Deleted “{r.name}”.", "green")

    def _enable(self, r: Routine, on: bool) -> None:
        r.enabled = on
        get_routines().save()
        self.notify.emit(f"“{r.name}” {'on' if on else 'paused'}.", "green")
        # repaint the badge/next-run line after the switch has glided
        from PySide6.QtCore import QTimer
        QTimer.singleShot(220, self.refresh)
