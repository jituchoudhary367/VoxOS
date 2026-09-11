import sys
import os
import asyncio
from typing import Optional

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, Signal

from voxos.ui.main_window import VoxOSOverlay
from voxos.ui.hotkey import HotkeyListener
from voxos.observability.logger import get_logger

# We need to bridge Asyncio and Qt. We'll use qasync
try:
    import qasync
except ImportError:
    qasync = None

log = get_logger(__name__)


class JarvisApp(QObject):
    """
    Main Application Controller for VoxOS.
    """
    
    # Signals to communicate between Voice Pipeline (async) and UI (sync)
    status_changed = Signal(str, str)
    transcript_received = Signal(str)

    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
        
        # UI Elements
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # Keep running in tray
        
        self.window = VoxOSOverlay()
        self.setup_tray()
        
        # Connect signals
        self.status_changed.connect(self.window.update_status)
        self.transcript_received.connect(self.window.append_transcript)
        
        # Hotkey Listener
        self.hotkey_listener = HotkeyListener(hotkey="ctrl+space")
        self.hotkey_listener.hotkey_pressed.connect(self.toggle_voice_mode)
        self.hotkey_listener.start()
        
        # Voice Pipeline State
        self.pipeline_running = False
        self._voice_task: Optional[asyncio.Task] = None

    def setup_tray(self):
        self.tray = QSystemTrayIcon()
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "logo.jpg")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
            self.app.setWindowIcon(icon)
        else:
            icon = self.app.style().standardIcon(self.app.style().StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setVisible(True)
        
        menu = QMenu()
        
        show_action = QAction("Show Overlay", self)
        show_action.triggered.connect(self.show_window)
        menu.addAction(show_action)
        
        hide_action = QAction("Hide Overlay", self)
        hide_action.triggered.connect(self.hide_window)
        menu.addAction(hide_action)
        
        menu.addSeparator()
        
        quit_action = QAction("Quit VoxOS", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        
        self.tray.setContextMenu(menu)
        
        # Click on tray icon
        self.tray.activated.connect(self.on_tray_click)

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def hide_window(self):
        self.window.hide()

    def toggle_window(self):
        if self.window.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def toggle_voice_mode(self):
        """Called when Ctrl+Space is pressed."""
        self.show_window()
        
        if self.pipeline_running:
            self.status_changed.emit("VoxOS: Sleeping", "#00FFCC")
            self.pipeline_running = False
            # In a real impl, we tell the VoicePipeline to pause/mute.
            # For now, we'll just log it.
            log.info("Voice mode deactivated via hotkey")
            if self._voice_task:
                self._voice_task.cancel()
        else:
            self.status_changed.emit("VoxOS: Listening...", "#FF00FF")
            self.pipeline_running = True
            log.info("Voice mode activated via hotkey")
            
            # Start the voice pipeline in the asyncio event loop
            if qasync:
                loop = asyncio.get_event_loop()
                self._voice_task = loop.create_task(self.start_voice_pipeline())
            else:
                log.error("qasync is required to run VoicePipeline with PySide6")

    async def start_voice_pipeline(self):
        """Bootstraps the pipecat voice pipeline."""
        try:
            from voxos.voice.pipeline import VoicePipeline
            self.voice = VoicePipeline(session_id=self.session_id)

            self.voice.on_transcript = lambda text: self.transcript_received.emit(text)
            self.voice.on_status = lambda text, color: self.status_changed.emit(text, color)
            
            await self.voice.start()
        except asyncio.CancelledError:
            if hasattr(self, 'voice'):
                await self.voice.stop()
        except Exception as e:
            log.error("voice_pipeline_crash", error=str(e))
            self.status_changed.emit("VoxOS: Error", "#FF0000")

    def quit(self):
        log.info("Shutting down VoxOS...")
        self.hotkey_listener.stop()
        if self._voice_task:
            self._voice_task.cancel()
        self.app.quit()


def run_ui(session_id: str):
    """Entry point for the PySide6 UI."""
    if qasync is None:
        raise ImportError("qasync is required for VoxOS UI. Install with `pip install qasync`.")
        
    app = QApplication.instance() or QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    voxos = JarvisApp(session_id=session_id)
    voxos.show_window()
    
    with loop:
        loop.run_forever()
