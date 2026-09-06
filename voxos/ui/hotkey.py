import keyboard
from PySide6.QtCore import QObject, Signal, QThread
import time
from typing import Callable

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class HotkeyListener(QThread):
    """
    Listens for a global hotkey and emits a Qt Signal.
    We run this in a QThread so it doesn't block the Qt event loop.
    """
    hotkey_pressed = Signal()

    def __init__(self, hotkey: str = "ctrl+space", parent=None):
        super().__init__(parent)
        self.hotkey = hotkey
        self._running = True

    def run(self):
        log.info("hotkey_listener_started", hotkey=self.hotkey)
        
        # keyboard.add_hotkey registers a callback on a background thread.
        # We catch it and emit our Qt Signal to bounce back to the main thread safely.
        def on_activate():
            self.hotkey_pressed.emit()
            
        try:
            keyboard.add_hotkey(self.hotkey, on_activate)
            while self._running:
                time.sleep(0.1)
        except Exception as e:
            log.error("hotkey_listener_error", error=str(e))
        finally:
            # Cleanup
            try:
                keyboard.remove_hotkey(self.hotkey)
            except Exception:
                pass
            log.info("hotkey_listener_stopped")

    def stop(self):
        self._running = False
        self.wait()
