"""
Vox's desktop look: warm white paper, terracotta for action, sage green for
"live" and "done".

Every colour in the UI comes from PALETTE; widgets that paint themselves read
the same values, so the stylesheet and the painted parts never drift apart.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

PALETTE = {
    "bg": "#FBF8F4",           # warm white window
    "surface": "#FFFFFF",      # cards
    "surface_alt": "#F5EFE7",  # sidebar, inputs at rest
    "border": "#EADFD3",
    "border_strong": "#DCCBBA",
    "text": "#2B2420",
    "text_muted": "#7D7068",
    "text_faint": "#A89A90",
    "terracotta": "#C4623F",
    "terracotta_dark": "#A34E30",
    "terracotta_soft": "#F7E4DB",
    "green": "#3F6B4F",
    "green_dark": "#2F5440",
    "green_soft": "#E2EDE4",
    "sage": "#8BAE93",
    "amber": "#B7791F",
    "amber_soft": "#FBEFD9",
    "amber_light": "#E7B872",
    "danger": "#B42318",
    "danger_soft": "#FBE4E1",
}


def color(name: str, alpha: int | None = None) -> QColor:
    c = QColor(PALETTE[name])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def _first_installed(*families: str) -> str:
    installed = set(QFontDatabase.families())
    return next((f for f in families if f in installed), families[-1])


class Fonts:
    """Resolved once a QApplication exists."""

    body = "Segoe UI"
    display = "Georgia"
    icons = "Segoe MDL2 Assets"

    @classmethod
    def resolve(cls) -> None:
        cls.body = _first_installed("Segoe UI Variable Text", "Segoe UI", "Arial")
        cls.display = _first_installed("Georgia", "Cambria", cls.body)
        cls.icons = _first_installed("Segoe Fluent Icons", "Segoe MDL2 Assets", cls.body)


def icon_font(size: int = 14) -> QFont:
    font = QFont(Fonts.icons)
    font.setPixelSize(size)
    return font


class Icon:
    """Segoe Fluent / MDL2 glyphs (both ship with Windows and share codepoints)."""

    CHAT = ""
    MIC = ""
    PROFILE = ""
    SETTINGS = ""
    SEND = ""
    DELETE = ""
    ADD = ""
    EYE = ""
    EYE_OFF = ""
    COPY = ""
    REFRESH = ""
    FOLDER = ""
    LOCK = ""
    CHECK = ""
    WARNING = ""
    SPARKLE = ""
    GLOBE = ""
    CLOCK = ""
    KEY = ""
    DATABASE = ""
    SPEAKER = ""
    POWER = ""
    DOWNLOAD = ""
    CHART = ""
    SCREEN = ""
    ROUTINE = ""


def stylesheet() -> str:
    p = PALETTE
    body = Fonts.body
    display = Fonts.display
    return f"""
    QWidget {{
        color: {p['text']};
        font-family: '{body}';
        font-size: 14px;
    }}
    QWidget#root, QWidget#page, QScrollArea#page_scroll > QWidget > QWidget {{
        background: {p['bg']};
    }}
    QScrollArea {{ background: transparent; border: none; }}
    QToolTip {{
        background: {p['text']}; color: {p['bg']}; border: none;
        padding: 6px 8px; border-radius: 6px;
    }}

    /* ── sidebar ─────────────────────────────── */
    QFrame#sidebar {{
        background: {p['surface_alt']};
        border-right: 1px solid {p['border']};
    }}
    QLabel#brand {{
        font-family: '{display}'; font-size: 26px; font-weight: 600;
        color: {p['text']};
    }}
    QLabel#brand_sub {{ color: {p['text_muted']}; font-size: 12px; }}
    QLabel#nav_section {{
        color: {p['text_faint']}; font-size: 11px; font-weight: 600;
        letter-spacing: 1px;
    }}

    /* ── type ────────────────────────────────── */
    QLabel#page_title {{
        font-family: '{display}'; font-size: 30px; color: {p['text']};
    }}
    QLabel#page_sub {{ color: {p['text_muted']}; font-size: 14px; }}
    QLabel#card_title {{ font-size: 16px; font-weight: 600; color: {p['text']}; }}
    QLabel#card_sub {{ color: {p['text_muted']}; font-size: 13px; }}
    QLabel#field_label {{ font-size: 13px; font-weight: 600; color: {p['text']}; }}
    QLabel#field_help {{ font-size: 12px; color: {p['text_muted']}; }}
    QLabel#muted {{ color: {p['text_muted']}; }}
    QLabel#mono {{ font-family: 'Cascadia Mono', 'Consolas'; font-size: 12px; color: {p['text_muted']}; }}

    /* ── cards ───────────────────────────────── */
    QFrame#card {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 16px;
    }}
    QFrame#row {{
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}
    QFrame#row:hover {{ border-color: {p['border_strong']}; }}
    QFrame#divider {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}

    /* ── inputs ──────────────────────────────── */
    QLineEdit, QComboBox, QDoubleSpinBox {{
        background: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        padding: 9px 12px;
        font-size: 14px;
        selection-background-color: {p['terracotta_soft']};
        selection-color: {p['text']};
    }}
    QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover {{ border-color: {p['border_strong']}; }}
    QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {p['terracotta']};
        background: {p['surface']};
    }}
    QLineEdit[dirty="true"] {{ border: 1px solid {p['amber']}; }}
    QLineEdit#chat_input {{
        background: {p['surface']}; border-radius: 24px; padding: 12px 20px; font-size: 15px;
    }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{
        background: {p['surface']}; border: 1px solid {p['border']};
        selection-background-color: {p['terracotta_soft']}; selection-color: {p['text']};
        outline: none; padding: 4px;
    }}

    /* ── buttons ─────────────────────────────── */
    QPushButton {{
        background: {p['surface']};
        border: 1px solid {p['border_strong']};
        border-radius: 10px;
        padding: 9px 16px;
        font-weight: 600;
        color: {p['text']};
    }}
    QPushButton:hover {{ background: {p['surface_alt']}; }}
    QPushButton:pressed {{ background: {p['border']}; }}
    QPushButton:disabled {{ color: {p['text_faint']}; border-color: {p['border']}; }}
    QPushButton[kind="primary"] {{
        background: {p['terracotta']}; border: 1px solid {p['terracotta']}; color: white;
    }}
    QPushButton[kind="primary"]:hover {{ background: {p['terracotta_dark']}; border-color: {p['terracotta_dark']}; }}
    QPushButton[kind="primary"]:disabled {{ background: {p['border_strong']}; border-color: {p['border_strong']}; color: white; }}
    QPushButton[kind="green"] {{
        background: {p['green']}; border: 1px solid {p['green']}; color: white;
    }}
    QPushButton[kind="green"]:hover {{ background: {p['green_dark']}; }}
    QPushButton[kind="green"]:disabled {{ background: {p['border_strong']}; border-color: {p['border_strong']}; color: white; }}
    QPushButton[kind="ghost"]:disabled {{ color: {p['text_faint']}; background: transparent; }}
    QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {p['text_muted']}; padding: 6px 10px; }}
    QPushButton[kind="ghost"]:hover {{ background: {p['surface_alt']}; color: {p['text']}; }}
    QPushButton[kind="danger"] {{ background: transparent; border: 1px solid {p['border_strong']}; color: {p['danger']}; }}
    QPushButton[kind="danger"]:hover {{ background: {p['danger_soft']}; border-color: {p['danger']}; }}
    QPushButton[kind="icon"] {{
        font-family: '{Fonts.icons}'; font-size: 14px; font-weight: normal;
        background: transparent; border: none; border-radius: 8px;
        padding: 0; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
        color: {p['text_muted']};
    }}
    QPushButton[kind="icon"]:hover {{ background: {p['surface_alt']}; color: {p['terracotta']}; }}
    QPushButton[kind="icon_danger"] {{
        font-family: '{Fonts.icons}'; font-size: 14px; font-weight: normal;
        background: transparent; border: none; border-radius: 8px;
        padding: 0; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
        color: {p['text_faint']};
    }}
    QPushButton[kind="icon_danger"]:hover {{ background: {p['danger_soft']}; color: {p['danger']}; }}
    QPushButton#send_btn {{
        font-family: '{Fonts.icons}'; font-size: 16px; font-weight: normal;
        background: {p['terracotta']}; color: white; border: none; border-radius: 24px;
        min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px; padding: 0;
    }}
    QPushButton#send_btn:hover {{ background: {p['terracotta_dark']}; }}
    QPushButton#send_btn:disabled {{ background: {p['border_strong']}; }}
    QPushButton#chip {{
        background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 16px;
        padding: 7px 14px; font-weight: 500; color: {p['text']};
    }}
    QPushButton#chip:hover {{ border-color: {p['terracotta']}; color: {p['terracotta_dark']}; background: {p['terracotta_soft']}; }}

    /* ── scrollbars ──────────────────────────── */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {p['text_faint']}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
    QScrollBar:horizontal {{ height: 0; }}
    """
