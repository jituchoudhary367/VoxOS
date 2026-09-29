"""
Profile: everything Vox keeps about the user, in one place, under their control.

- who they are (name, id, since when) — name editable;
- facts Vox learned — add, remove one, forget all;
- habits (sites, tools, hours) — read, clear;
- saved logins in Windows Credential Manager — add, remove (secrets never shown);
- long-term memories in the database — read, delete one, delete all;
- export of the local profile as JSON.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QRectF, QUrl, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from voxos.core.profile.store import data_dir, get_profile
from voxos.observability.logger import get_logger
from voxos.ui.theme import PALETTE, Fonts, Icon, color
from voxos.ui.widgets import (
    Card, PageHeader, SecretField, button, fade_in, glyph, icon_button, label,
)

log = get_logger(__name__)

_DB_TIMEOUT = 20.0  # Neon wakes from idle in ~5-15 s
_NAME_FACT = re.compile(r"^(?:the\s+)?user'?s name is\s", re.IGNORECASE)


def _when(stamp: str | None) -> str:
    if not stamp:
        return "—"
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%d %b %Y, %H:%M")
    except ValueError:
        return stamp


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if w := item.widget():
            w.setParent(None)
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())


def _empty(text: str) -> QLabel:
    lbl = label(text, "muted", wrap=True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {PALETTE['text_faint']}; padding: 18px; border: 1px dashed {PALETTE['border_strong']};"
        "border-radius: 10px;"
    )
    return lbl


def _row(text: str, meta: str = "", glyph_char: str = "") -> tuple[QFrame, QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("row")
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 10, 8, 10)
    row.setSpacing(12)
    if glyph_char:
        row.addWidget(glyph(glyph_char, 14, "sage"))
    col = QVBoxLayout()
    col.setSpacing(2)
    main = label(text, wrap=True)
    main.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    main.setStyleSheet("background: transparent; border: none;")
    col.addWidget(main)
    if meta:
        sub = label(meta, "field_help")
        sub.setStyleSheet(f"background: transparent; border: none; color: {PALETTE['text_muted']}; font-size: 12px;")
        col.addWidget(sub)
    row.addLayout(col, 1)
    return frame, row


class _LoginDialog(QDialog):
    """Add a saved login. The password goes straight to the OS keyring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save a login")
        self.setMinimumWidth(440)
        self.setStyleSheet(f"QDialog {{ background: {PALETTE['bg']}; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(10)
        title = label("Save a login", "page_title")
        title.setStyleSheet(f"font-family: '{Fonts.display}'; font-size: 22px;")
        col.addWidget(title)
        col.addWidget(label("Stored in Windows Credential Manager. Vox can fill it only on the "
                            "sites you list, and never sees or says the password.", "card_sub", wrap=True))
        col.addSpacing(6)
        self.name = QLineEdit()
        self.name.setPlaceholderText("amazon")
        self.domains = QLineEdit()
        self.domains.setPlaceholderText("amazon.in, amazon.com")
        self.username = QLineEdit()
        self.username.setPlaceholderText("you@example.com")
        self.password = SecretField(placeholder="Password")
        self.password.copy.hide()
        for caption, widget in (("Name", self.name), ("Sites", self.domains),
                                ("Username or email", self.username), ("Password", self.password)):
            col.addWidget(label(caption, "field_label"))
            col.addWidget(widget)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {PALETTE['danger']};")
        self.error.hide()
        col.addWidget(self.error)
        col.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        save = button("Save login", "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        col.addLayout(buttons)

    def _save(self) -> None:
        from voxos.vault.store import get_vault

        try:
            get_vault().add(
                self.name.text(),
                username=self.username.text().strip(),
                password=self.password.text(),
                domains=self.domains.text().replace(";", ",").split(","),
            )
        except Exception as e:  # noqa: BLE001 — ValueError for bad input, keyring errors otherwise
            self.error.setText(str(e))
            self.error.show()
            return
        self.accept()


class ProfilePage(QWidget):
    notify = Signal(str, str)  # message, tone

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self._build()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
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

        header = PageHeader("Profile", "What Vox knows about you. It stays on this computer; "
                                       "change or delete any of it whenever you like.")
        export = button("Export", None, "Save your profile as a JSON file")
        export.clicked.connect(self._export)
        folder = button("Open data folder", "ghost")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        header.actions.addWidget(folder)
        header.actions.addWidget(export)
        col.addWidget(header)

        col.addWidget(self._identity_card())

        # facts
        self.facts_card = Card("What Vox has learned",
                               "Facts and preferences Vox remembers between conversations.", Icon.SPARKLE)
        forget_all = button("Forget all", "danger")
        forget_all.clicked.connect(self._forget_all)
        self.facts_card.add_action(forget_all)
        add_row = QHBoxLayout()
        self.fact_input = QLineEdit()
        self.fact_input.setPlaceholderText("Teach Vox something, e.g. “I prefer Brave over Chrome”")
        self.fact_input.returnPressed.connect(self._add_fact)
        add = button("Add", "primary")
        add.clicked.connect(self._add_fact)
        add_row.addWidget(self.fact_input, 1)
        add_row.addWidget(add)
        self.facts_card.body.addLayout(add_row)
        self.facts_box = QVBoxLayout()
        self.facts_box.setSpacing(8)
        self.facts_card.body.addLayout(self.facts_box)
        col.addWidget(self.facts_card)

        # habits
        self.habits_card = Card("Habits", "Counted from what you ask Vox to do.", Icon.CLOCK)
        clear_habits = button("Clear", "danger")
        clear_habits.clicked.connect(self._clear_habits)
        self.habits_card.add_action(clear_habits)
        self.habits_box = QHBoxLayout()
        self.habits_box.setSpacing(16)
        self.habits_card.body.addLayout(self.habits_box)
        col.addWidget(self.habits_card)

        # logins
        self.logins_card = Card("Saved logins",
                                "Kept in Windows Credential Manager. Only the names and sites are shown here.",
                                Icon.LOCK)
        add_login = button("+  Add login", "green")
        add_login.clicked.connect(self._add_login)
        self.logins_card.add_action(add_login)
        self.logins_box = QVBoxLayout()
        self.logins_box.setSpacing(8)
        self.logins_card.body.addLayout(self.logins_box)
        col.addWidget(self.logins_card)

        # memories
        self.memories_card = Card("Long-term memories", "Stored in your database.", Icon.DATABASE)
        refresh = icon_button(Icon.REFRESH, "Reload memories")
        refresh.clicked.connect(self.load_memories)
        delete_all = button("Delete all", "danger")
        delete_all.clicked.connect(self._delete_all_memories)
        self.memories_card.add_action(refresh)
        self.memories_card.add_action(delete_all)
        self.memories_box = QVBoxLayout()
        self.memories_box.setSpacing(8)
        self.memories_card.body.addLayout(self.memories_box)
        col.addWidget(self.memories_card)

        col.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _identity_card(self) -> Card:
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(20)
        self.avatar = QLabel("?")
        self.avatar.setFixedSize(72, 72)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {PALETTE['terracotta']},"
            f" stop:1 {PALETTE['green']}); color: white; border-radius: 36px;"
            f"font-family: '{Fonts.display}'; font-size: 30px;"
        )
        row.addWidget(self.avatar)

        info = QVBoxLayout()
        info.setSpacing(6)
        name_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("What should Vox call you?")
        self.name_input.setStyleSheet(
            f"font-family: '{Fonts.display}'; font-size: 22px; background: transparent;"
            "border: 1px solid transparent; padding: 4px 6px;"
        )
        self.name_input.returnPressed.connect(self._save_name)
        self.name_input.editingFinished.connect(self._save_name)
        name_row.addWidget(self.name_input, 1)
        info.addLayout(name_row)
        self.meta = label("", "field_help", wrap=True)
        info.addWidget(self.meta)
        id_row = QHBoxLayout()
        id_row.setSpacing(6)
        self.user_id = label("", "mono")
        self.user_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        id_row.addWidget(self.user_id)
        id_row.addStretch(1)
        info.addLayout(id_row)
        row.addLayout(info, 1)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_facts = self._stat("Facts")
        self.stat_logins = self._stat("Logins")
        self.stat_memories = self._stat("Memories")
        for w in (self.stat_facts, self.stat_logins, self.stat_memories):
            stats.addWidget(w)
        row.addLayout(stats)
        card.body.addLayout(row)
        return card

    def _stat(self, caption: str) -> QFrame:
        box = QFrame()
        box.setFixedSize(92, 70)
        box.setStyleSheet(f"QFrame {{ background: {PALETTE['surface_alt']}; border-radius: 12px; }}")
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 8, 0, 8)
        col.setSpacing(0)
        value = QLabel("0")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setStyleSheet(
            f"background: transparent; font-size: 24px; font-weight: 600; color: {PALETTE['terracotta_dark']};"
        )
        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet(f"background: transparent; font-size: 11px; color: {PALETTE['text_muted']};")
        col.addWidget(value)
        col.addWidget(cap)
        box.value = value  # type: ignore[attr-defined]
        return box

    # ── refresh ──────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-read everything. Called whenever the page is shown."""
        self._render_identity()
        self._render_facts()
        self._render_habits()
        self._render_logins()
        self.load_memories()

    def _render_identity(self) -> None:
        profile = get_profile()
        name = profile.name or ""
        if not self.name_input.hasFocus():
            self.name_input.setText(name)
        self.avatar.setText((name[:1] or "?").upper())
        self.meta.setText(f"With Vox since {_when(profile.first_seen)}  ·  last active {_when(profile.last_seen)}")
        self.user_id.setText(f"ID  {profile.user_id}")
        self.stat_facts.value.setText(str(len(profile.facts)))

    def _render_facts(self) -> None:
        _clear(self.facts_box)
        facts = list(reversed(get_profile().facts))
        if not facts:
            self.facts_box.addWidget(_empty("Nothing yet. Vox learns as you talk, or add a fact above."))
        for i, fact in enumerate(facts):
            frame, row = _row(fact["text"], f"Updated {_when(fact.get('updated'))}", Icon.CHECK)
            remove = icon_button(Icon.DELETE, "Forget this", danger=True)
            remove.clicked.connect(lambda _=False, t=fact["text"]: self._forget_fact(t))
            row.addWidget(remove)
            self.facts_box.addWidget(frame)
            if i < 8:
                fade_in(frame, 220, 6, delay=i * 30)

    def _render_habits(self) -> None:
        _clear(self.habits_box)
        habits = get_profile().habits
        columns = (
            ("Sites", Icon.GLOBE, [(s, n) for s, n in habits["sites"].most_common(5)]),
            ("Tools", Icon.SETTINGS, [(t.replace("_", " ").replace(".", " · "), n)
                                      for t, n in habits["tools"].most_common(5)]),
            ("Active hours", Icon.CLOCK, [(f"{int(h):02d}:00", n) for h, n in habits["hours"].most_common(5)]),
        )
        for title, glyph_char, items in columns:
            box = QFrame()
            box.setStyleSheet(f"QFrame {{ background: {PALETTE['surface_alt']}; border-radius: 12px; }}")
            col = QVBoxLayout(box)
            col.setContentsMargins(16, 14, 16, 14)
            col.setSpacing(8)
            head = QHBoxLayout()
            head.addWidget(glyph(glyph_char, 13, "terracotta"))
            head.addWidget(label(title, "field_label"))
            head.addStretch(1)
            col.addLayout(head)
            if not items:
                col.addWidget(label("No data yet", "field_help"))
            top = max((n for _, n in items), default=1)
            for name, n in items:
                col.addWidget(_Bar(name, n, n / top))
            col.addStretch(1)
            self.habits_box.addWidget(box, 1)

    def _render_logins(self) -> None:
        _clear(self.logins_box)
        try:
            from voxos.vault.store import get_vault
            logins = get_vault().list()
        except Exception as e:  # noqa: BLE001 — keyring missing or locked
            self.logins_box.addWidget(_empty(f"Credential Manager unavailable: {e}"))
            self.stat_logins.value.setText("–")
            return
        self.stat_logins.value.setText(str(len(logins)))
        if not logins:
            self.logins_box.addWidget(_empty("No saved logins. Add one so Vox can sign in for you."))
        for item in logins:
            frame, row = _row(item["name"], "  ·  ".join(item["domains"]), Icon.LOCK)
            remove = icon_button(Icon.DELETE, "Delete this login", danger=True)
            remove.clicked.connect(lambda _=False, n=item["name"]: self._remove_login(n))
            row.addWidget(remove)
            self.logins_box.addWidget(frame)

    # ── actions: facts, name, habits ─────────────────────────────────────────

    def _save_name(self) -> None:
        profile = get_profile()
        name = " ".join(self.name_input.text().split())
        if name == (profile.name or ""):
            return
        # Only the name facts go; other facts that mention the name stay.
        profile.facts = [f for f in profile.facts if not _NAME_FACT.match(f["text"])]
        profile.name = None
        if name:
            profile.add_facts([f"User's name is {name}"])
        profile.save()
        self._render_identity()
        self._render_facts()
        self.notify.emit(f"Vox will call you {name}." if name else "Name removed.", "green")

    def _add_fact(self) -> None:
        text = self.fact_input.text().strip()
        if not text:
            return
        profile = get_profile()
        if profile.add_facts([text]):
            profile.save()
            self.fact_input.clear()
            self._render_identity()
            self._render_facts()
            self.notify.emit("Vox will remember that.", "green")
        else:
            self.notify.emit("That fact is empty or too long (240 characters max).", "amber")

    def _forget_fact(self, text: str) -> None:
        profile = get_profile()
        before = len(profile.facts)
        profile.facts = [f for f in profile.facts if f["text"] != text]
        if _NAME_FACT.match(text):
            profile.name = None
        if len(profile.facts) != before:
            profile.save()
        self._render_identity()
        self._render_facts()
        self.notify.emit("Forgotten.", "green")

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _forget_all(self) -> None:
        if not self._confirm("Forget everything?", "Vox will forget every fact it has learned about you, "
                                                    "including your name. Habits and logins stay."):
            return
        profile = get_profile()
        profile.facts.clear()
        profile.name = None
        profile.save()
        self._render_identity()
        self._render_facts()
        self.notify.emit("All facts forgotten.", "green")

    def _clear_habits(self) -> None:
        if not self._confirm("Clear habits?", "Reset the counts of sites, tools and active hours."):
            return
        profile = get_profile()
        for counter in profile.habits.values():
            counter.clear()
        profile.save()
        self._render_habits()
        self.notify.emit("Habits cleared.", "green")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export profile", "vox-profile.json", "JSON (*.json)")
        if not path:
            return
        data = get_profile().to_dict()
        try:
            from voxos.vault.store import get_vault
            data["saved_logins"] = get_vault().list()  # names and sites only
        except Exception:  # noqa: BLE001
            pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.notify.emit("Profile exported.", "green")

    # ── actions: logins ──────────────────────────────────────────────────────

    def _add_login(self) -> None:
        dialog = _LoginDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._render_logins()
            self.notify.emit("Login saved to Credential Manager.", "green")

    def _remove_login(self, name: str) -> None:
        if not self._confirm("Delete login?", f"Remove the saved login “{name}” from Credential Manager?"):
            return
        from voxos.vault.store import get_vault
        get_vault().remove(name)
        self._render_logins()
        self.notify.emit(f"Deleted “{name}”.", "green")

    # ── actions: long-term memories (database) ───────────────────────────────

    def load_memories(self) -> None:
        _clear(self.memories_box)
        self.memories_box.addWidget(_empty("Loading memories…"))
        try:
            asyncio.ensure_future(self._load_memories())
        except RuntimeError:  # no event loop (tests)
            _clear(self.memories_box)
            self.memories_box.addWidget(_empty("Memories load when Vox is running."))

    async def _load_memories(self) -> None:
        from voxos.database.engine import get_session_factory
        from voxos.database.repositories.memory_repo import MemoryRepository

        async def fetch():
            async with get_session_factory()() as session:
                return await MemoryRepository(session).list_for_user(uuid.UUID(get_profile().user_id), limit=200)

        try:
            entries = await asyncio.wait_for(fetch(), _DB_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — offline, bad URL, no table
            log.warning("profile_memories_unavailable", error=str(e) or type(e).__name__)
            _clear(self.memories_box)
            self.memories_box.addWidget(_empty("Couldn’t reach the database. Check the Postgres URL in Settings."))
            self.stat_memories.value.setText("–")
            return
        _clear(self.memories_box)
        self.stat_memories.value.setText(str(len(entries)))
        if not entries:
            self.memories_box.addWidget(_empty("No long-term memories stored yet."))
        for i, entry in enumerate(entries):
            created = entry.created_at.astimezone().strftime("%d %b %Y") if entry.created_at else ""
            frame, row = _row(entry.content, f"{entry.memory_type}  ·  {created}", Icon.DATABASE)
            remove = icon_button(Icon.DELETE, "Delete this memory", danger=True)
            remove.clicked.connect(lambda _=False, mid=entry.id: asyncio.ensure_future(self._delete_memory(mid)))
            row.addWidget(remove)
            self.memories_box.addWidget(frame)
            if i < 8:
                fade_in(frame, 220, 6, delay=i * 30)

    async def _delete_memory(self, memory_id) -> None:
        from voxos.database.engine import get_session_factory
        from voxos.database.repositories.memory_repo import MemoryRepository

        async def run():
            async with get_session_factory()() as session:
                await MemoryRepository(session).delete(memory_id)
                await session.commit()

        try:
            await asyncio.wait_for(run(), _DB_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            self.notify.emit(f"Couldn’t delete: {e}", "danger")
            return
        self.notify.emit("Memory deleted.", "green")
        self.load_memories()

    def _delete_all_memories(self) -> None:
        if not self._confirm("Delete all memories?", "Every long-term memory Vox stored in the database "
                                                     "will be deleted. This can’t be undone."):
            return
        asyncio.ensure_future(self._delete_all())

    async def _delete_all(self) -> None:
        from voxos.database.engine import get_session_factory
        from voxos.database.repositories.memory_repo import MemoryRepository

        async def run():
            async with get_session_factory()() as session:
                n = await MemoryRepository(session).delete_all_for_user(uuid.UUID(get_profile().user_id))
                await session.commit()
                return n

        try:
            n = await asyncio.wait_for(run(), _DB_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            self.notify.emit(f"Couldn’t delete: {e}", "danger")
            return
        self.notify.emit(f"Deleted {n} memories.", "green")
        self.load_memories()


class _Bar(QWidget):
    """One habit: name, count, and a terracotta bar that grows in."""

    def __init__(self, name: str, count: int, share: float, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.name, self.count, self.share = name, count, share
        self._grow = 0.0
        anim = QVariantAnimation(self)
        anim.setDuration(600)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._set)
        anim.start()

    def _set(self, v) -> None:
        self._grow = float(v)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(Fonts.body)
        font.setPixelSize(12)
        p.setFont(font)
        p.setPen(color("text"))
        p.drawText(QRectF(0, 0, self.width() - 40, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, self.width() - 44))
        p.setPen(color("text_muted"))
        p.drawText(QRectF(0, 0, self.width(), 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   str(self.count))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color("border"))
        p.drawRoundedRect(QRectF(0, 22, self.width(), 6), 3, 3)
        p.setBrush(color("terracotta"))
        p.drawRoundedRect(QRectF(0, 22, max(6.0, self.width() * self.share * self._grow), 6), 3, 3)
