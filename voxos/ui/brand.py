"""
The Vox mark: a "V" drawn as a voice waveform.

Rounded terracotta bars stand where the two strokes of a V would be: read
one way it is the letter, read another it is sound. A green dot floats in the
V's opening. With it the mark spells "Vo", the dot is the same "listening"
light the app shows when Vox is live, and the whole reads as a figure with
raised arms: speak, and it is done.

(Two-tone red/green bars were tried first and read as stock candlesticks.)

All geometry lives here, in a unit square, so the SVG files, the Windows icon
and the animated mark inside the app are one drawing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TERRACOTTA = "#C4623F"
TERRACOTTA_DEEP = "#A34E30"
GREEN = "#3F6B4F"
GREEN_LIGHT = "#5C8A6B"
CREAM = "#FBF8F4"
INK = "#2B2420"

# count: (step between bars, bar width, bar length, V depth), unit square
_SPEC = {
    7: (0.086, 0.064, 0.25, 0.36),
    5: (0.118, 0.090, 0.27, 0.34),
    3: (0.190, 0.145, 0.30, 0.28),
}
# Mostly a clean V; a slight swell at the vertex keeps it a voice, not a chart.
_RHYTHM = {
    7: (1.0, 1.0, 1.02, 1.1, 1.02, 1.0, 1.0),
    5: (1.0, 1.02, 1.1, 1.02, 1.0),
    3: (1.0, 1.12, 1.0),
}
BAR_RHYTHM = _RHYTHM[7]


@dataclass(frozen=True)
class Bar:
    cx: float      # centre x, 0..1
    cy: float      # centre y, 0..1
    width: float
    length: float
    colour: str


def bars(count: int = 7) -> list[Bar]:
    """The mark's bars in a unit square: 7 normally, 5 or 3 for small icons."""
    step, width, length, depth = _SPEC[count]
    half = step * (count - 1) / 2
    top = 0.5 - depth / 2  # the V's centreline: edges high, middle low
    out = []
    for i in range(count):
        x = 0.5 - half + i * step
        dip = 1 - abs(x - 0.5) / half
        colour = "bar"
        out.append(Bar(x, top + depth * dip, width, length * _RHYTHM[count][i], colour))
    return out


# count: (dot centre y, dot radius) — sits in the V's mouth, clear of the bars
_DOT = {7: (0.378, 0.062), 5: (0.32, 0.07), 3: (0.33, 0.08)}


def dot(count: int = 7) -> tuple[float, float, float]:
    """(cx, cy, r) of the green dot in a unit square."""
    cy, r = _DOT[count]
    return 0.5, cy, r


def bar_rect(bar: Bar, size: float, scale: float = 1.0, length_k: float = 1.0) -> tuple[float, float, float, float]:
    """(x, y, w, h) of a bar in a `size` square, scaled about the centre."""
    w, h = bar.width * size * scale, bar.length * size * scale * length_k
    x = (0.5 + (bar.cx - 0.5) * scale) * size - w / 2
    y = (0.5 + (bar.cy - 0.5) * scale) * size - h / 2
    return x, y, w, h


def squircle_points(size: float, inset: float = 0.0, n: float = 5.0, steps: int = 96) -> list[tuple[float, float]]:
    """A superellipse |x|^n + |y|^n = 1: the continuous corner of modern app icons."""
    a = size / 2 - inset
    c = size / 2
    pts = []
    for k in range(steps):
        t = 2 * math.pi * k / steps
        ct, st = math.cos(t), math.sin(t)
        x = c + a * math.copysign(abs(ct) ** (2 / n), ct)
        y = c + a * math.copysign(abs(st) ** (2 / n), st)
        pts.append((x, y))
    return pts


def _squircle_path(size: float, inset: float) -> str:
    pts = squircle_points(size, inset)
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"


def _defs() -> str:
    return (
        '<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#D2774F"/><stop offset="1" stop-color="#AE4E2E"/>'
        "</linearGradient>"
        '<radialGradient id="dot" cx="0.4" cy="0.35" r="0.75">'
        f'<stop offset="0" stop-color="#6A957A"/><stop offset="1" stop-color="{GREEN}"/>'
        "</radialGradient>"
    )


def _bars_svg(size: float, count: int, scale: float, mono: str | None = None) -> str:
    parts = []
    for b in bars(count):
        x, y, w, h = bar_rect(b, size, scale)
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{w / 2:.2f}" '
            f'fill="{mono or "url(#bar)"}"/>'
        )
    cx, cy, r = dot(count)
    cx, cy, r = (0.5 + (cx - 0.5) * scale) * size, (0.5 + (cy - 0.5) * scale) * size, r * size * scale
    parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{mono or "url(#dot)"}"/>')
    return "\n  ".join(parts)


def svg_mark(size: int = 512, mono: str | None = None) -> str:
    """The bare mark, for light backgrounds. `mono` draws it in one colour."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">\n'
        f"  <title>Vox</title>\n  <defs>{_defs()}</defs>\n  {_bars_svg(size, 7, 1.3, mono)}\n</svg>\n"
    )


def svg_app_icon(size: int = 512, count: int = 7) -> str:
    """The app tile: warm paper squircle, hairline edge, a breath of terracotta light."""
    scale = {7: 1.12, 5: 1.14, 3: 1.18}[count]
    tile = _squircle_path(size, size * 0.035)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <title>Vox</title>
  <defs>
    {_defs()}
    <linearGradient id="paper" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFDFA"/><stop offset="1" stop-color="#F1E6D9"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.66" r="0.6">
      <stop offset="0" stop-color="{TERRACOTTA}" stop-opacity="0.12"/>
      <stop offset="1" stop-color="{TERRACOTTA}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <path d="{tile}" fill="url(#paper)"/>
  <path d="{tile}" fill="url(#glow)"/>
  <path d="{tile}" fill="none" stroke="#D9C6B3" stroke-width="{max(1.0, size / 200):.2f}"/>
  {_bars_svg(size, count, scale)}
</svg>
'''


def svg_lockup(height: int = 160, dark: bool = False) -> str:
    """Mark + wordmark + line, for the splash screen, README and slides."""
    ink = CREAM if dark else INK
    sub = "#B9ADA3" if dark else "#7D7068"
    width = int(height * 3.4)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <title>Vox</title>
  <defs>{_defs()}</defs>
  {_bars_svg(height, 7, 1.3)}
  <text x="{height * 1.0:.1f}" y="{height * 0.635:.1f}" font-family="Georgia" font-size="{height * 0.54:.1f}"
        letter-spacing="{height * -0.01:.2f}" fill="{ink}">Vox</text>
  <text x="{height * 1.02:.1f}" y="{height * 0.83:.1f}" font-family="Segoe UI" font-size="{height * 0.1:.1f}"
        font-weight="600" letter-spacing="{height * 0.025:.2f}" fill="{sub}">YOUR VOICE. YOUR COMPUTER.</text>
</svg>
'''


def animated_lengths(t: float, energy: float) -> list[float]:
    """Length multipliers for the living mark. energy 0 = still, 1 = speaking."""
    out = []
    for i in range(7):
        wave = 0.5 + 0.5 * math.sin(t * 7.0 + i * 0.9) * math.sin(t * 2.3 + i * 1.7)
        out.append(1 - 0.35 * energy + 0.75 * energy * wave)
    return out


# ── export ───────────────────────────────────────────────────────────────────

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _count_for(px: int) -> int:
    """Fewer, bolder bars as the icon shrinks, so it never turns to mush."""
    return 3 if px <= 24 else 5 if px <= 48 else 7


def render_png(svg: str, px: int) -> bytes:
    """Rasterise an SVG string to PNG bytes (needs a QGuiApplication)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, px, px))
    painter.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    """A Windows .ico holding one PNG per size (Vista+ format)."""
    import struct

    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for px in sizes:
        data = pngs[px]
        dim = 0 if px >= 256 else px
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return header + entries + blobs


def app_icon_pngs() -> dict[int, bytes]:
    return {px: render_png(svg_app_icon(512, _count_for(px)), px) for px in ICON_SIZES}


def export_assets(folder) -> list[str]:
    """Write every brand file into `folder`. Returns the file names."""
    from pathlib import Path

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "vox-mark.svg": svg_mark(),
        "vox-mark-mono-light.svg": svg_mark(mono=CREAM),
        "vox-mark-mono-dark.svg": svg_mark(mono=INK),
        "vox-app-icon.svg": svg_app_icon(),
        "vox-lockup.svg": svg_lockup(),
        "vox-lockup-dark.svg": svg_lockup(dark=True),
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    pngs = app_icon_pngs()
    (folder / "vox.ico").write_bytes(ico_bytes(pngs))
    (folder / "vox-app-icon-256.png").write_bytes(pngs[256])
    (folder / "vox-app-icon-1024.png").write_bytes(render_png(svg_app_icon(1024), 1024))
    return [*files, "vox.ico", "vox-app-icon-256.png", "vox-app-icon-1024.png"]


if __name__ == "__main__":  # python -m voxos.ui.brand  → regenerate voxos/ui/assets/
    import sys
    from pathlib import Path

    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    out = Path(__file__).parent / "assets"
    for name in export_assets(out):
        print(out / name)
