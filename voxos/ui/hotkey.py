"""
Global hotkey listener with tap / hold gestures.

A tap toggles a hands-free listening session. A hold is push-to-talk: the
microphone opens while the key is down and closes on release, which makes
background noise and self-echo nearly impossible.
"""

from __future__ import annotations

import enum
import time

import keyboard
from PySide6.QtCore import QThread, Signal

from voxos.observability.logger import get_logger

log = get_logger(__name__)

DEFAULT_HOTKEY = "ctrl+space"
DEFAULT_HOLD_THRESHOLD = 0.35


class HotkeyGesture(enum.Enum):
    """How a completed hotkey press should be interpreted."""

    TAP = "tap"
    HOLD = "hold"


def classify_gesture(held_secs: float, hold_threshold: float) -> HotkeyGesture:
    """Classify a completed press by how long the key was down.

    Args:
        held_secs: Seconds between press and release.
        hold_threshold: Durations at or above this are holds.

    Returns:
        TAP for a quick press, HOLD for a deliberate one.
    """
    return HotkeyGesture.HOLD if held_secs >= hold_threshold else HotkeyGesture.TAP


class HotkeyListener(QThread):
    """
    Listens for a global hotkey and emits Qt Signals.

    Runs in a QThread so the global keyboard hook never blocks the Qt event
    loop. Three signals, because push-to-talk has to act on the press itself
    rather than waiting to learn how the press ends:

    - `hold_started`: the key has been down past the threshold — open the mic.
    - `hotkey_released`: the key came up after a hold — close the mic.
    - `hotkey_tapped`: the key came up before the threshold — toggle a session.
    """

    hotkey_tapped = Signal()
    hold_started = Signal()
    hotkey_released = Signal()

    def __init__(
        self,
        hotkey: str = DEFAULT_HOTKEY,
        hold_threshold: float = DEFAULT_HOLD_THRESHOLD,
        parent=None,
    ):
        super().__init__(parent)
        self.hotkey = hotkey
        self.hold_threshold = hold_threshold
        self._running = True
        self._pressed_at: float | None = None
        self._hold_announced = False

    def run(self):
        log.info(
            "hotkey_listener_started",
            hotkey=self.hotkey,
            hold_threshold=self.hold_threshold,
        )

        hooks = []
        try:
            hooks.append(keyboard.on_press_key(self._trigger_key, self._on_press))
            hooks.append(keyboard.on_release_key(self._trigger_key, self._on_release))

            while self._running:
                # A hold has to be announced while the key is still down, so
                # the deadline is checked here rather than on release.
                self._announce_hold_if_due()
                time.sleep(0.02)
        except Exception as e:
            log.error("hotkey_listener_error", error=str(e))
        finally:
            for hook in hooks:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
            log.info("hotkey_listener_stopped")

    @property
    def _trigger_key(self) -> str:
        """The final key of the combination, which is the one we hook."""
        return self.hotkey.split("+")[-1].strip()

    def _modifiers_held(self) -> bool:
        """True when every modifier in the combination is currently down."""
        parts = [p.strip() for p in self.hotkey.split("+")[:-1]]
        return all(keyboard.is_pressed(part) for part in parts)

    def _on_press(self, _event) -> None:
        if self._pressed_at is not None:
            return  # key repeat while already held
        if not self._modifiers_held():
            return
        self._pressed_at = time.monotonic()
        self._hold_announced = False

    def _announce_hold_if_due(self) -> None:
        if self._pressed_at is None or self._hold_announced:
            return
        if time.monotonic() - self._pressed_at >= self.hold_threshold:
            self._hold_announced = True
            self.hold_started.emit()

    def _on_release(self, _event) -> None:
        if self._pressed_at is None:
            return
        held_secs = time.monotonic() - self._pressed_at
        self._pressed_at = None

        if classify_gesture(held_secs, self.hold_threshold) is HotkeyGesture.HOLD:
            self.hotkey_released.emit()
        else:
            self.hotkey_tapped.emit()
        self._hold_announced = False

    def stop(self):
        self._running = False
        self.wait()
