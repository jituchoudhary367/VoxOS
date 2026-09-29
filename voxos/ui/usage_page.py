"""
Usage: how Vox has been doing today, and how much quota is left.

Numbers come from the activity log (one row per request, kept 30 days)
and, for Groq, from the key pool's live counters and the rate-limit headers
of each key's last response. Refreshed whenever the page is shown and every
few seconds while it is visible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from voxos.ui.activity import get_activity_log
from voxos.ui.charts import BarChart, Meter, StatTile
from voxos.ui.envfile import mask
from voxos.ui.theme import PALETTE, Icon
from voxos.ui.widgets import Card, PageHeader, label

# Groq free tier per key, per model (Sept 2026): used to turn "remaining" into a share.
DAILY_REQUESTS = 1000
TOKENS_PER_MINUTE = 8000


def key_stats() -> tuple[str, list[dict[str, Any]]]:
    """(mode, one dict per Groq key) from the live router, without touching the network."""
    try:
        from voxos.config import get_settings
        from voxos.llm.key_pool import KeyPoolProvider
        from voxos.llm.router import get_llm_router

        settings = get_settings().llm
        provider = get_llm_router().provider
    except Exception:  # noqa: BLE001 — not configured yet
        return "hybrid", []
    pool = provider if isinstance(provider, KeyPoolProvider) else getattr(provider, "primary", None)
    if not isinstance(pool, KeyPoolProvider):
        return settings.mode, []
    keys = settings.all_groq_keys()
    out = []
    for stat, slot in zip(pool.stats(), pool._slots):
        state = getattr(slot.provider, "rate_limit_state", None) or {}
        out.append({
            **stat,
            "masked": mask(keys[stat["key"]]) if stat["key"] < len(keys) else f"key {stat['key'] + 1}",
            "remaining_requests": state.get("remaining_requests"),
            "remaining_tokens_min": state.get("remaining_tokens", stat.get("remaining_tokens")),
        })
    return settings.mode, out


def _hour(h: int) -> str:
    return datetime(2000, 1, 1, h).strftime("%I %p").lstrip("0")


class UsagePage(QWidget):
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
        col.addWidget(PageHeader("Usage", "How Vox is doing today, and how much quota you have left."))

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_requests = StatTile("Requests today")
        self.t_success = StatTile("Done without errors")
        self.t_speed = StatTile("Typical response")
        self.t_tools = StatTile("Actions taken")
        for t in (self.t_requests, self.t_success, self.t_speed, self.t_tools):
            tiles.addWidget(t, 1)
        col.addLayout(tiles)

        chart_card = Card("Requests by hour", "Today. Hover a bar for the count.", Icon.CLOCK)
        self.chart = BarChart(lambda i, v: f"{_hour(i)}  ·  {int(v)} request{'s' if v != 1 else ''}")
        self.chart.empty_text = "No requests yet today. Ask Vox something in Chat."
        chart_card.body.addWidget(self.chart)
        self.chart_note = label("", "field_help")
        chart_card.body.addWidget(self.chart_note)
        col.addWidget(chart_card)

        speed_card = Card("Response time", "Your last 20 requests, oldest to newest, in seconds.", Icon.SPARKLE)
        self.speed = BarChart(lambda i, v: f"{v:.1f} s")
        self.speed.setMinimumHeight(150)
        self.speed.empty_text = "No requests yet."
        speed_card.body.addWidget(self.speed)
        col.addWidget(speed_card)

        self.keys_card = Card("Groq keys", "Live from this session. Quotas refill daily; per-minute limits refill "
                              "every minute.", Icon.KEY)
        self.keys_grid = QGridLayout()
        self.keys_grid.setHorizontalSpacing(14)
        self.keys_grid.setVerticalSpacing(14)
        self.keys_card.body.addLayout(self.keys_grid)
        col.addWidget(self.keys_card)

        self.mode_card = Card("Models", "", Icon.LOCK)
        self.mode_text = label("", wrap=True)
        self.mode_card.body.addWidget(self.mode_text)
        col.addWidget(self.mode_card)
        col.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start(4000)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def refresh(self) -> None:
        s = get_activity_log().summary()
        n = s["requests"]
        self.t_requests.set(f"{n:,}", "since midnight")
        rate = s["success_rate"]
        self.t_success.set("–" if rate is None else f"{rate * 100:.0f}%", "of today's requests")
        med = s["median_seconds"]
        self.t_speed.set("–" if med is None else f"{med:.1f} s", "median, today")
        self.t_tools.set(f"{s['tools']:,}", "tools run today")

        now = datetime.now().hour
        self.chart.set_data(s["per_hour"], [_hour(h) for h in range(24)], highlight=now)
        if n:
            busiest = max(range(24), key=lambda h: s["per_hour"][h])
            self.chart_note.setText(f"Busiest hour: {_hour(busiest)} with {s['per_hour'][busiest]} "
                                    f"request{'s' if s['per_hour'][busiest] != 1 else ''}.")
        else:
            self.chart_note.setText("")
        recent = s["recent_seconds"]
        self.speed.set_data(recent, [str(i + 1) for i in range(len(recent))],
                            highlight=len(recent) - 1 if recent else None)
        self._render_keys()

    def _render_keys(self) -> None:
        while self.keys_grid.count():
            item = self.keys_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mode, keys = key_stats()
        if not keys:
            text = "No Groq keys in use." if mode != "local" else "Local mode: Groq isn't used."
            self.keys_grid.addWidget(label(text, "field_help"), 0, 0)
        for i, k in enumerate(keys):
            self.keys_grid.addWidget(self._key_box(k), i // 2, i % 2)
        self.keys_grid.setColumnStretch(0, 1)
        self.keys_grid.setColumnStretch(1, 1)
        try:
            from voxos.config import get_settings

            llm = get_settings().llm
            where = {"cloud": "Cloud: Groq only.", "local": "Local: everything runs on this PC.",
                     "hybrid": "Hybrid: Groq first, your local model when Groq is out of quota."}[llm.mode]
            local = f" Local model: {llm.ollama_model}." if llm.mode != "cloud" else ""
            self.mode_text.setText(f"{where} Fast: {llm.fast_model}. Strong: {llm.strong_model}.{local}")
        except Exception:  # noqa: BLE001
            self.mode_text.setText("")

    def _key_box(self, k: dict[str, Any]) -> QFrame:
        box = QFrame()
        box.setObjectName("keybox")
        box.setStyleSheet(
            f"QFrame#keybox {{ background: {PALETTE['surface_alt']}; border-radius: 12px; }}"
            " QLabel { background: transparent; }"
        )
        col = QVBoxLayout(box)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)
        head = QHBoxLayout()
        name = QLabel(k["masked"])
        name.setStyleSheet(f"font-family: 'Cascadia Mono', 'Consolas'; font-size: 13px; font-weight: 600;"
                           f" color: {PALETTE['text']};")
        if k["disabled"]:
            state, tone = "Rejected", "danger"
        elif k["cooling_s"]:
            state, tone = f"Resting {int(max(k['cooling_s'].values()))} s", "amber"
        else:
            state, tone = "Ready", "green"
        chip = QLabel(state)
        chip.setStyleSheet(f"background: {PALETTE[tone + '_soft']}; color: {PALETTE[tone]}; border-radius: 9px;"
                           "padding: 2px 9px; font-size: 11px; font-weight: 700;")
        head.addWidget(name, 1)
        head.addWidget(chip)
        col.addLayout(head)

        req = k.get("remaining_requests")
        col.addWidget(label("Requests left today" + (f": {req:,}" if req is not None else ": not known yet"),
                            "field_help"))
        m1 = Meter()
        m1.set_fraction(None if req is None else req / DAILY_REQUESTS)
        col.addWidget(m1)
        tok = k.get("remaining_tokens_min")
        col.addWidget(label("Tokens left this minute" + (f": {tok:,}" if tok is not None else ": not known yet"),
                            "field_help"))
        m2 = Meter()
        m2.set_fraction(None if tok is None else tok / TOKENS_PER_MINUTE)
        col.addWidget(m2)
        col.addWidget(label(f"{k.get('calls', 0):,} calls  ·  {k.get('tokens_used', 0):,} tokens this session",
                            "field_help"))
        return box
