"""
Screenshot utility for the Vision module.
"""

from __future__ import annotations

import io
from PIL import ImageGrab
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)


def capture_screen(region: tuple[int, int, int, int] | None = None) -> bytes:
    """
    Capture the screen and return as PNG bytes.
    """
    log.debug("capturing_screenshot", region=region)
    try:
        img = ImageGrab.grab(bbox=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.error("screenshot_failed", error=str(e))
        raise
