"""
Settings: every key and knob Vox reads from .env, editable in place.

Changes are tracked field by field (an edited field gets an amber border and
the save bar slides in), saved without disturbing the rest of the file, and
applied on the next start — the page offers that restart itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

from voxos.ui import ollama
from voxos.ui.envfile import EnvFile, test_groq_key
from voxos.ui.theme import PALETTE, Icon
from voxos.ui.widgets import (
    Card, PageHeader, SecretField, Segmented, SlimProgress, Toggle, button, fade_in,
    glyph, icon_button, label, repolish,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    title: str
    help: str = ""
    kind: str = "text"  # text | secret | toggle | number
    default: str = ""
    placeholder: str = ""


SECTIONS: list[tuple[str, str, str, list[FieldSpec]]] = [
    ("Cloud models", "Groq-hosted models, used in Cloud and Hybrid mode. Keys are managed above.", Icon.SPARKLE, [
        FieldSpec("OPENROUTER_API_KEY", "OpenRouter API key", "Optional fallback provider.", "secret",
                  placeholder="sk-or-…"),
        FieldSpec("LLM_FAST_MODEL", "Fast model", "Quick replies and routing.",
                  default="openai/gpt-oss-20b"),
        FieldSpec("LLM_STRONG_MODEL", "Strong model", "Planning and hard requests.",
                  default="openai/gpt-oss-120b"),
        FieldSpec("LLM_VISION_MODEL", "Vision model", "Reads screenshots of your screen.",
                  default="qwen/qwen3.8-27b"),
    ]),
    ("Voice", "How Vox hears and speaks.", Icon.MIC, [
        FieldSpec("DEEPGRAM_API_KEY", "Deepgram API key", "Speech-to-text and the Aura voice.", "secret",
                  placeholder="Deepgram token"),
        FieldSpec("DEEPGRAM_TTS_VOICE", "Aura voice", "English voice for spoken replies.",
                  default="aura-2-thalia-en"),
        FieldSpec("EDGE_TTS_VOICE", "Fallback voice", "Used for Hindi and Hinglish replies.",
                  default="en-US-AriaNeural"),
        FieldSpec("VOICE_HOTKEY", "Hotkey", "Tap for hands-free, hold to talk.", default="ctrl+space"),
        FieldSpec("VOICE_WAKE_WORD_ENABLED", "Wake word", "Listen for the wake word in the background.",
                  "toggle", default="true"),
        FieldSpec("VOICE_WAKE_WORD_SENSITIVITY", "Wake word sensitivity",
                  "Higher wakes more easily, and more by mistake.", "number", default="0.6"),
    ]),
    ("Storage", "Where conversations and long-term memories live.", Icon.DATABASE, [
        FieldSpec("POSTGRES_URL", "Postgres URL", "Neon or any Postgres connection string.", "secret",
                  placeholder="postgresql://…"),
        FieldSpec("REDIS_URL", "Redis URL", "Optional. Leave empty to use local Redis.", "secret",
                  placeholder="rediss://…"),
    ]),
]

MODES = (("cloud", "Cloud"), ("hybrid", "Hybrid"), ("local", "Local"))
MODE_HELP = {
    "cloud": "Groq's hosted models only. Fastest and smartest; needs the internet.",
    "hybrid": "Groq first. When it is down or out of quota, Vox switches to your local model by itself.",
    "local": "Everything runs on this PC: private, and works offline. Slower on a laptop.",
}
LOCAL_FIELDS = [
    FieldSpec("LLM_MODE", "Where Vox thinks", kind="mode", default="hybrid"),
    FieldSpec("OLLAMA_BASE_URL", "Ollama address", "Where the Ollama server listens.",
              default="http://localhost:11434"),
    FieldSpec("OLLAMA_MODEL", "Local model", "One all-rounder for chat, planning, vision and code.",
              "model", default="qwen3.5:4b"),
]
JOB_FIELDS = [
    FieldSpec("OLLAMA_FAST_MODEL", "Quick replies", "Routing and short answers.", "model"),
    FieldSpec("OLLAMA_STRONG_MODEL", "Planning", "Multi-step requests.", "model"),
    FieldSpec("OLLAMA_VISION_MODEL", "Vision", "Reading your screen; must accept images.", "model"),
]


def _spawn(coro):
    """Run a coroutine on the qasync loop; drop it quietly when there is none (tests)."""
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return None


_TRUE = {"1", "true", "yes", "on"}


class _GroqKeyRow(QFrame):
    removed = Signal(object)
    edited = Signal()

    def __init__(self, value: str, index: int):
        super().__init__()
        self.setObjectName("row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)
        self.number = label("", "mono")
        self.number.setFixedWidth(22)
        self.field = SecretField(value, "gsk_…")
        self.field.input.setStyleSheet("background: transparent; border: none; padding: 4px 2px;")
        self.field.edited.connect(self.edited.emit)
        self.status = QLabel()
        self.status.setMinimumWidth(10)
        self.test_btn = button("Test", "ghost", "Send one tiny request with this key")
        remove = icon_button(Icon.DELETE, "Remove key", danger=True)
        remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(self.number)
        layout.addWidget(self.field, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.test_btn)
        layout.addWidget(remove)
        self.set_index(index)
        self.set_status("", "")

    def set_index(self, index: int) -> None:
        self.number.setText(f"{index + 1:02d}")
        self.number.setToolTip("Primary key" if index == 0 else "Extra key")

    def set_status(self, state: str, detail: str) -> None:
        tones = {"ok": ("green", "green_soft", "Working"), "limited": ("amber", "amber_soft", "Limited"),
                 "invalid": ("danger", "danger_soft", "Invalid"), "error": ("danger", "danger_soft", "Error"),
                 "testing": ("text_muted", "surface_alt", "Testing…")}
        if state not in tones:
            self.status.hide()
            return
        fg, bg, text = tones[state]
        self.status.setText(text)
        self.status.setToolTip(detail)
        self.status.setStyleSheet(
            f"background: {PALETTE[bg]}; color: {PALETTE[fg]}; border-radius: 10px;"
            "padding: 3px 10px; font-size: 12px; font-weight: 600;"
        )
        self.status.show()


class SettingsPage(QWidget):
    restart_requested = Signal()
    notify = Signal(str, str)  # message, tone

    def __init__(self, env_path: str | Path = ".env", parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.env_path = Path(env_path)
        self._inputs: dict[str, QWidget] = {}
        self._original: dict[str, str] = {}
        self._key_rows: list[_GroqKeyRow] = []
        self._original_keys: list[str] = []
        self._build()
        self.reload()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("page_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("page")
        col = QVBoxLayout(content)
        col.setContentsMargins(44, 36, 44, 36)
        col.setSpacing(20)

        header = PageHeader("Settings", "Your keys, models and voice. Saved to your local .env file.")
        col.addWidget(header)

        col.addWidget(self._groq_card())
        col.addWidget(self._local_card())
        for title, sub, glyph_char, fields in SECTIONS:
            col.addWidget(self._section_card(title, sub, glyph_char, fields))
        col.addWidget(self._system_card())
        col.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.save_bar = self._save_bar()
        outer.addWidget(self.save_bar)
        self.save_bar.hide()

    def _groq_card(self) -> Card:
        card = Card("Groq API keys",
                    "Each key from a different Groq account adds a full quota. Vox uses the one with "
                    "the most room left and skips any that are throttled.", Icon.KEY)
        test_all = button("Test all", None, "Check every key")
        test_all.clicked.connect(self._test_all)
        card.add_action(test_all)
        self.keys_box = QVBoxLayout()
        self.keys_box.setSpacing(8)
        card.body.addLayout(self.keys_box)
        add = button("+  Add another key", "ghost")
        add.clicked.connect(lambda: self._add_key_row("", focus=True))
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _section_card(self, title: str, sub: str, glyph_char: str, fields: list[FieldSpec]) -> Card:
        card = Card(title, sub, glyph_char)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(18)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        for i, spec in enumerate(fields):
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(spec.title, "field_label"))
            if spec.help:
                text.addWidget(label(spec.help, "field_help", wrap=True))
            grid.addLayout(text, i, 0, Qt.AlignmentFlag.AlignTop)
            widget = self._make_input(spec)
            self._inputs[spec.key] = widget
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter if spec.kind == "toggle" else Qt.AlignmentFlag.AlignVCenter
            grid.addWidget(widget, i, 1, align)
        card.body.addSpacing(4)
        card.body.addLayout(grid)
        return card

    def _make_input(self, spec: FieldSpec) -> QWidget:
        if spec.kind == "secret":
            w = SecretField(placeholder=spec.placeholder)
            w.edited.connect(self._changed)
        elif spec.kind == "toggle":
            w = Toggle()
            w.toggled.connect(lambda _: self._changed())
        elif spec.kind == "number":
            w = QDoubleSpinBox()
            w.setRange(0.05, 1.0)
            w.setSingleStep(0.05)
            w.setDecimals(2)
            w.setFixedWidth(120)
            w.valueChanged.connect(lambda _: self._changed())
        elif spec.kind == "mode":
            w = Segmented(list(MODES))
            w.changed.connect(lambda _: self._changed())
        elif spec.kind == "model":
            w = QComboBox()
            w.setEditable(True)
            w.setMinimumWidth(240)
            w.lineEdit().setPlaceholderText(spec.default or "Same as the local model")
            w.lineEdit().textEdited.connect(lambda _: self._changed())
            w.activated.connect(lambda _: self._changed())
        else:
            w = QLineEdit()
            w.setPlaceholderText(spec.placeholder or spec.default)
            w.textEdited.connect(lambda _: self._changed())
        return w

    def _system_card(self) -> Card:
        card = Card("Configuration file", "Everything above is stored here, next to Vox.", Icon.FOLDER)
        self.path_label = label("", "mono")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self.path_label)
        row = QHBoxLayout()
        open_file = button("Open in editor")
        open_file.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.env_path.resolve()))))
        reload_btn = button("Reload from disk", "ghost")
        reload_btn.clicked.connect(self._reload_clicked)
        restart = button("Restart Vox", "green", "Restart so saved changes take effect")
        restart.clicked.connect(self.restart_requested.emit)
        row.addWidget(open_file)
        row.addWidget(reload_btn)
        row.addStretch(1)
        row.addWidget(restart)
        card.body.addLayout(row)
        return card

    def _save_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background: {PALETTE['surface']}; border-top: 1px solid {PALETTE['border']}; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(44, 12, 44, 12)
        row.addWidget(glyph(Icon.WARNING, 14, "amber"))
        self.save_msg = label("You have unsaved changes")
        self.save_msg.setStyleSheet("border: none; font-weight: 600;")
        row.addWidget(self.save_msg)
        row.addStretch(1)
        revert = button("Discard", "ghost")
        revert.clicked.connect(self.reload)
        self.save_btn = button("Save changes", "primary")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(revert)
        row.addWidget(self.save_btn)
        return bar

    # ── local models ─────────────────────────────────────────────────────────

    def _all_specs(self) -> list[FieldSpec]:
        return [*LOCAL_FIELDS, *JOB_FIELDS, *(s for _, _, _, fields in SECTIONS for s in fields)]

    def _local_card(self) -> Card:
        card = Card("Local models",
                    "Run Vox on this PC with Ollama: private, offline, no quotas.", Icon.LOCK)
        self._installed: list[str] = []
        self._pulling = False

        # where Vox thinks
        mode_spec, url_spec, model_spec = LOCAL_FIELDS
        mode = self._make_input(mode_spec)
        self._inputs[mode_spec.key] = mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(mode)
        mode_row.addStretch(1)
        card.body.addLayout(mode_row)
        self.mode_help = label("", "field_help", wrap=True)
        card.body.addWidget(self.mode_help)

        # Ollama status
        status = QFrame()
        status.setObjectName("row")
        row = QHBoxLayout(status)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)
        self.ollama_dot = QLabel()
        self.ollama_dot.setFixedSize(10, 10)
        self.ollama_text = label("Checking Ollama…")
        self.ollama_text.setStyleSheet("background: transparent; border: none; font-weight: 600;")
        self.install_btn = button("Install Ollama", "primary")
        self.install_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ollama.DOWNLOAD_URL)))
        self.install_btn.hide()
        check = button("Check again", "ghost")
        check.clicked.connect(self.refresh_local)
        row.addWidget(self.ollama_dot)
        row.addWidget(self.ollama_text, 1)
        row.addWidget(self.install_btn)
        row.addWidget(check)
        card.body.addWidget(status)

        # address + model
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)
        for i, spec in enumerate((url_spec, model_spec)):
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(spec.title, "field_label"))
            text.addWidget(label(spec.help, "field_help", wrap=True))
            grid.addLayout(text, i, 0, Qt.AlignmentFlag.AlignTop)
            w = self._make_input(spec)
            self._inputs[spec.key] = w
            grid.addWidget(w, i, 1, Qt.AlignmentFlag.AlignVCenter)
        card.body.addLayout(grid)

        card.body.addWidget(self._recommendation_box())

        # optional per-job models
        toggle = button("Use a different model per job", "ghost")
        card.body.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._jobs_box = QWidget()
        jobs = QGridLayout(self._jobs_box)
        jobs.setContentsMargins(0, 0, 0, 0)
        jobs.setHorizontalSpacing(24)
        jobs.setVerticalSpacing(12)
        jobs.setColumnStretch(0, 2)
        jobs.setColumnStretch(1, 3)
        for i, spec in enumerate(JOB_FIELDS):
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(spec.title, "field_label"))
            text.addWidget(label(spec.help, "field_help", wrap=True))
            jobs.addLayout(text, i, 0, Qt.AlignmentFlag.AlignTop)
            w = self._make_input(spec)
            self._inputs[spec.key] = w
            jobs.addWidget(w, i, 1, Qt.AlignmentFlag.AlignVCenter)
        self._jobs_box.hide()
        card.body.addWidget(self._jobs_box)

        def flip() -> None:
            self._jobs_box.setVisible(not self._jobs_box.isVisible())
            if self._jobs_box.isVisible():
                fade_in(self._jobs_box, 220, 8)
        toggle.clicked.connect(flip)
        return card

    def _recommendation_box(self) -> QFrame:
        hw = ollama.hardware()
        self._hardware = hw
        self._rec = ollama.recommend(hw.ram_gb)
        box = QFrame()
        box.setObjectName("rec_box")
        box.setStyleSheet(
            f"QFrame#rec_box {{ background: {PALETTE['green_soft']}; border-radius: 14px; }}"
            " QLabel { background: transparent; }"
        )
        col = QVBoxLayout(box)
        col.setContentsMargins(18, 16, 18, 16)
        col.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(glyph(Icon.SPARKLE, 14, "green"))
        tag = label("RECOMMENDED FOR THIS PC")
        tag.setStyleSheet(f"font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {PALETTE['green_dark']};")
        head.addWidget(tag)
        head.addStretch(1)
        col.addLayout(head)

        name_row = QHBoxLayout()
        name = QLabel(self._rec.name)
        name.setStyleSheet(f"font-family: 'Cascadia Mono', 'Consolas'; font-size: 18px; font-weight: 600;"
                           f" color: {PALETTE['text']};")
        size = label(f"{self._rec.size_gb:.1f} GB", "field_help")
        name_row.addWidget(name)
        name_row.addSpacing(8)
        name_row.addWidget(size)
        name_row.addStretch(1)
        self.rec_state = QLabel()
        name_row.addWidget(self.rec_state)
        col.addLayout(name_row)
        col.addWidget(label(self._rec.why, wrap=True))
        col.addWidget(label(f"This PC: {hw.summary()}", "field_help", wrap=True))
        self.rec_warning = label("", wrap=True)
        self.rec_warning.setStyleSheet(f"color: {PALETTE['amber']}; font-size: 12px;")
        self.rec_warning.hide()
        col.addWidget(self.rec_warning)

        self.pull_progress = SlimProgress()
        self.pull_progress.hide()
        self.pull_text = label("", "field_help")
        self.pull_text.hide()
        col.addWidget(self.pull_progress)
        col.addWidget(self.pull_text)

        buttons = QHBoxLayout()
        self.pull_btn = button(f"Download  ·  {self._rec.size_gb:.1f} GB", "green")
        self.pull_btn.clicked.connect(lambda: _spawn(self._pull(self._rec.name)))
        use = button("Use this model", None)
        use.clicked.connect(self._use_recommended)
        self.test_btn = button("Test speed", "ghost", "One short answer, timed")
        self.test_btn.clicked.connect(lambda: _spawn(self._speed_test()))
        buttons.addWidget(self.pull_btn)
        buttons.addWidget(use)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        col.addLayout(buttons)
        self.test_result = label("", wrap=True)
        self.test_result.setStyleSheet(f"font-size: 13px; color: {PALETTE['green_dark']};")
        self.test_result.hide()
        col.addWidget(self.test_result)
        return box

    def _show_mode_help(self) -> None:
        mode = self._inputs.get("LLM_MODE")
        if mode is not None and hasattr(self, "mode_help"):
            self.mode_help.setText(MODE_HELP[mode.value()])

    def _use_recommended(self) -> None:
        self._inputs["OLLAMA_MODEL"].setEditText(self._rec.name)
        self._changed()

    def _ollama_url(self) -> str:
        return self._read_input(LOCAL_FIELDS[1]) or LOCAL_FIELDS[1].default

    def refresh_local(self) -> None:
        _spawn(self._refresh_local())

    async def _refresh_local(self) -> None:
        state = await ollama.status(self._ollama_url())
        self._installed = state.models if state.running else []
        if state.running:
            n = len(state.models)
            self._set_ollama(True, f"Ollama {state.version} is running  ·  {n} model{'s' if n != 1 else ''} installed")
        else:
            self._set_ollama(False, "Ollama isn't running on this PC. Install it, or start it from the Start menu.")
        for spec in (LOCAL_FIELDS[2], *JOB_FIELDS):
            combo: QComboBox = self._inputs[spec.key]
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(self._installed)
            combo.setEditText(current)
            combo.blockSignals(False)
        self._show_rec_state()

    def _set_ollama(self, running: bool, text: str) -> None:
        tone = PALETTE["green"] if running else PALETTE["amber"]
        self.ollama_dot.setStyleSheet(f"background: {tone}; border-radius: 5px;")
        self.ollama_text.setText(text)
        self.install_btn.setVisible(not running)
        self.pull_btn.setEnabled(running and not self._pulling)
        self.test_btn.setEnabled(running)

    def _show_rec_state(self) -> None:
        installed = ollama.is_installed(self._rec.name, self._installed)
        self.rec_state.setText("Installed" if installed else "")
        self.rec_state.setStyleSheet(
            f"background: {PALETTE['surface']}; color: {PALETTE['green']}; border-radius: 10px;"
            "padding: 3px 10px; font-size: 12px; font-weight: 700;"
        )
        self.rec_state.setVisible(installed)
        self.pull_btn.setVisible(not installed)
        need = self._rec.size_gb + 1.0
        if not installed and self._hardware.free_gb < need:
            self.rec_warning.setText(
                f"Only {self._hardware.free_gb:.1f} GB of memory is free right now. Close some apps "
                f"(browser tabs use a lot) before using a local model; it needs about {need:.0f} GB."
            )
            self.rec_warning.show()
        else:
            self.rec_warning.hide()

    async def _pull(self, model: str) -> None:
        if self._pulling:
            return
        self._pulling = True
        self.pull_btn.setEnabled(False)
        self.pull_progress.show()
        self.pull_text.show()
        self.pull_progress.set_value(0)

        def progress(fraction: float, status: str) -> None:
            self.pull_progress.set_value(fraction)
            pct = f"  ·  {fraction * 100:.0f}%" if fraction else ""
            self.pull_text.setText(f"{status.capitalize()}{pct}")

        try:
            await ollama.pull(self._ollama_url(), model, progress)
        except Exception as e:  # noqa: BLE001
            self.notify.emit(f"Download failed: {e}", "danger")
            self.pull_text.setText(f"Download failed: {e}")
        else:
            self.pull_text.setText(f"{model} is ready.")
            self.notify.emit(f"{model} downloaded.", "green")
            self._inputs["OLLAMA_MODEL"].setEditText(model)
            self._changed()
        finally:
            self._pulling = False
            self.pull_progress.hide()
            await self._refresh_local()

    async def _speed_test(self) -> None:
        model = self._read_input(LOCAL_FIELDS[2]) or self._rec.name
        self.test_btn.setEnabled(False)
        self.test_result.show()
        self.test_result.setText(f"Asking {model}… the first answer also loads the model, so it takes longer.")
        try:
            result = await ollama.speed_test(self._ollama_url(), model)
        except Exception as e:  # noqa: BLE001
            self.test_result.setText(f"Test failed: {e}")
        else:
            self.test_result.setText(
                f"{result.tokens_per_sec:.1f} tokens/sec  ·  answered in {result.seconds:.1f} s\n“{result.reply}”"
            )
        finally:
            self.test_btn.setEnabled(True)
        fade_in(self.test_result, 240, 6)

    # ── groq key rows ────────────────────────────────────────────────────────

    def _add_key_row(self, value: str, focus: bool = False) -> None:
        row = _GroqKeyRow(value, len(self._key_rows))
        row.removed.connect(self._remove_key_row)
        row.edited.connect(self._changed)
        row.test_btn.clicked.connect(lambda: self._test_rows([row]))
        self._key_rows.append(row)
        self.keys_box.addWidget(row)
        if focus:
            fade_in(row, 220, 8)
            row.field.input.setFocus()
            self._changed()

    def _remove_key_row(self, row: _GroqKeyRow) -> None:
        self._key_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        for i, r in enumerate(self._key_rows):
            r.set_index(i)
        self._changed()

    def _current_keys(self) -> list[str]:
        return [r.field.text() for r in self._key_rows if r.field.text()]

    def _test_all(self) -> None:
        self._test_rows([r for r in self._key_rows if r.field.text()])

    def _test_rows(self, rows: list[_GroqKeyRow]) -> None:
        rows = [r for r in rows if r.field.text()]
        if not rows:
            self.notify.emit("Add a key to test first.", "amber")
            return
        for r in rows:
            r.set_status("testing", "")
        asyncio.ensure_future(self._run_tests(rows))

    async def _run_tests(self, rows: list[_GroqKeyRow]) -> None:
        results = await asyncio.gather(*(test_groq_key(r.field.text()) for r in rows), return_exceptions=True)
        for row, result in zip(rows, results):
            if isinstance(result, BaseException):
                row.set_status("error", str(result))
            else:
                row.set_status(*result)

    # ── load / save ──────────────────────────────────────────────────────────

    def _reload_clicked(self) -> None:
        self.reload()
        self.notify.emit("Reloaded settings from disk.", "green")

    def reload(self) -> None:
        env = EnvFile.load(self.env_path)
        values = env.values()
        self.path_label.setText(str(self.env_path.resolve()))

        for row in list(self._key_rows):
            row.setParent(None)
            row.deleteLater()
        self._key_rows.clear()
        self._original_keys = env.groq_keys()
        for key in self._original_keys or [""]:
            self._add_key_row(key)

        for spec in self._all_specs():
            value = values.get(spec.key, spec.default)
            self._original[spec.key] = self._normalise(spec, value)
            self._write_input(spec, value)
        self._jobs_box.setVisible(any(self._read_input(f) for f in JOB_FIELDS))
        self._changed()
        self.refresh_local()

    def _write_input(self, spec: FieldSpec, value: str) -> None:
        w = self._inputs[spec.key]
        w.blockSignals(True)
        if spec.kind == "toggle":
            w.setChecked(value.strip().lower() in _TRUE)
            w.knob = 1.0 if w.isChecked() else 0.0
        elif spec.kind == "number":
            try:
                w.setValue(float(value or spec.default))
            except ValueError:
                w.setValue(float(spec.default))
        elif spec.kind == "mode":
            w.set_value(self._normalise(spec, value))
        elif spec.kind == "model":
            w.setEditText(value)
        else:
            w.setText(value)
        w.blockSignals(False)

    @staticmethod
    def _normalise(spec: FieldSpec, value: str) -> str:
        if spec.kind == "mode":
            value = value.strip().lower()
            return value if value in MODE_HELP else spec.default
        if spec.kind == "toggle":
            return "true" if value.strip().lower() in _TRUE else "false"
        if spec.kind == "number":
            try:
                return f"{float(value or spec.default):.2f}"
            except ValueError:
                return f"{float(spec.default):.2f}"
        return value.strip()

    def _read_input(self, spec: FieldSpec) -> str:
        w = self._inputs[spec.key]
        if spec.kind == "toggle":
            return "true" if w.isChecked() else "false"
        if spec.kind == "number":
            return f"{w.value():.2f}"
        if spec.kind == "mode":
            return w.value()
        if spec.kind == "model":
            return w.currentText().strip()
        return w.text().strip()

    def dirty_fields(self) -> list[str]:
        dirty = [
            spec.key for spec in self._all_specs()
            if self._read_input(spec) != self._original.get(spec.key, "")
        ]
        if self._current_keys() != self._original_keys:
            dirty.append("GROQ_API_KEYS")
        return dirty

    def _changed(self) -> None:
        dirty = set(self.dirty_fields())
        for key, w in self._inputs.items():
            target = w.input if isinstance(w, SecretField) else w
            if isinstance(target, QComboBox):
                target = target.lineEdit()
            if isinstance(target, QLineEdit):
                target.setProperty("dirty", key in dirty)
                repolish(target)
        self._show_mode_help()
        n = len(dirty)
        if n:
            self.save_msg.setText(f"{n} unsaved change{'s' if n != 1 else ''}")
            if self.save_bar.isHidden():
                self.save_bar.show()
                fade_in(self.save_bar, 200, 0)
        else:
            self.save_bar.hide()

    def save(self) -> None:
        env = EnvFile.load(self.env_path)
        changed = self.dirty_fields()
        for spec in self._all_specs():
            if spec.key in changed:
                env.set(spec.key, self._read_input(spec))
        if "GROQ_API_KEYS" in changed:
            env.set_groq_keys(self._current_keys())
        try:
            env.save()
        except OSError as e:
            self.notify.emit(f"Could not save: {e}", "danger")
            return
        self.reload()
        self.notify.emit("Saved. Restart Vox to use the new settings.", "green")
