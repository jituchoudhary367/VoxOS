import sys
import os
import asyncio
import subprocess
from typing import Optional

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

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
        
        # Start backend process
        self.backend_process: Optional[subprocess.Popen] = None
        self.start_backend()
        
        # UI Elements
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # Keep running in tray
        
        self.setup_tray()
        
        # Connect signals
        self.status_changed.connect(self._handle_status)
        self.transcript_received.connect(self._handle_transcript)
        
        # Hotkey Listener
        self.hotkey_listener = HotkeyListener(hotkey="ctrl+space")
        self.hotkey_listener.hotkey_pressed.connect(self.toggle_voice_mode)
        self.hotkey_listener.start()
        
        # Voice Pipeline State
        self.pipeline_running = False
        self._voice_task: Optional[asyncio.Task] = None
        
        # Setup Wake Word
        self.setup_wake_word()

    def _handle_status(self, text: str, color: str):
        if hasattr(self, 'dashboard_window') and self.dashboard_window:
            self.dashboard_window.update_status(text, color)
            
    def _handle_transcript(self, text: str):
        if hasattr(self, 'dashboard_window') and self.dashboard_window:
            self.dashboard_window.append_transcript(text)

    def start_backend(self):
        """Starts the FastAPI backend as a subprocess."""
        log.info("Starting VoxOS backend...")
        try:
            # We assume we are running in the context where `python -m voxos` or `uvicorn voxos.main:app` works
            self.backend_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "voxos.main:app", "--host", "127.0.0.1", "--port", "8000"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            log.info("Backend started successfully.")
        except Exception as e:
            log.error("backend_startup_failed", error=str(e))

    def stop_backend(self):
        """Stops the FastAPI backend."""
        if self.backend_process:
            log.info("Stopping VoxOS backend...")
            self.backend_process.terminate()
            try:
                self.backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.backend_process.kill()
            self.backend_process = None

    def setup_tray(self):
        self.tray = QSystemTrayIcon()
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "logo.jpg")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            from PySide6.QtWidgets import QStyle
            icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            
        self.app.setWindowIcon(icon)
        self.tray.setIcon(icon)
        self.tray.setVisible(True)
        
        # Load windows lazily to prevent blocking startup
        self.settings_window = None
        self.dashboard_window = None
        
        menu = QMenu()
        
        dash_action = QAction("Open Interaction Dashboard", self)
        dash_action.triggered.connect(self.show_dashboard)
        menu.addAction(dash_action)
        
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)
        
        menu.addSeparator()
        
        quit_action = QAction("Exit VoxOS", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        
        self.tray.setContextMenu(menu)
        
        # Click on tray icon opens dashboard by default
        self.tray.activated.connect(self.on_tray_click)

    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_dashboard()

    def show_dashboard(self):
        if not self.dashboard_window:
            from voxos.ui.dashboard import InteractionDashboard
            self.dashboard_window = InteractionDashboard()
            self.dashboard_window.chat_message_sent.connect(self._handle_chat_message)
            self.dashboard_window.voice_toggle_requested.connect(self.toggle_voice_mode)
        self.dashboard_window.show()
        self.dashboard_window.raise_()
        self.dashboard_window.activateWindow()

    def show_settings(self):
        if not self.settings_window:
            from voxos.ui.settings_window import SettingsWindow
            self.settings_window = SettingsWindow()
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def toggle_voice_mode(self):
        """Called when Ctrl+Space is pressed, or when Wake Word triggers."""
        self.show_dashboard()
        if hasattr(self, 'dashboard_window') and self.dashboard_window:
            self.dashboard_window.switch_mode(1)

        
        if self.pipeline_running:
            self.status_changed.emit("VoxOS: Wake Word Only", "#00FFCC")
            self.pipeline_running = False
            log.info("Voice mode deactivated via hotkey")
            if self._voice_task:
                self._voice_task.cancel()
            # Resume wake word engine after finishing interaction
            if hasattr(self, 'wake_word_engine'):
                self.wake_word_engine.resume()
        else:
            self.status_changed.emit("VoxOS: Listening...", "#FF00FF")
            self.pipeline_running = True
            log.info("Voice mode activated")
            
            # Pause wake word while actively talking to the AI
            if hasattr(self, 'wake_word_engine'):
                self.wake_word_engine.pause()
                
            if qasync:
                loop = asyncio.get_event_loop()
                self._voice_task = loop.create_task(self.start_voice_pipeline())
            else:
                log.error("qasync is required to run VoicePipeline with PySide6")

    def setup_wake_word(self):
        """Initializes the wake word engine."""
        try:
            from voxos.voice.wakeword import OpenWakeWordEngine
            self.wake_word_engine = OpenWakeWordEngine(callback=self._on_wake_word)
            self.wake_word_engine.start()
            log.info("Wake word engine started successfully.")
            self.status_changed.emit("VoxOS: Ready (Wake Word On)", "#00FFCC")
        except Exception as e:
            log.error("wake_word_engine_failed", error=str(e))
            self.status_changed.emit("VoxOS: Wake Word Failed", "#FF0000")

    def _on_wake_word(self):
        """Callback from the wake word detector thread."""
        # Since this comes from a background thread, we must schedule the toggle
        # in the main thread's event loop to safely update UI and start asyncio tasks.
        if qasync:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(self.toggle_voice_mode)

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
        finally:
            # Re-enable wake word listening if the pipeline ends on its own
            if self.pipeline_running:
                self.pipeline_running = False
                self.status_changed.emit("VoxOS: Wake Word Only", "#00FFCC")
            if hasattr(self, 'wake_word_engine'):
                self.wake_word_engine.resume()

    def _handle_chat_message(self, text: str):
        if qasync:
            loop = asyncio.get_event_loop()
            loop.create_task(self._run_chat_task(text))

    async def _run_chat_task(self, text: str):
        self.status_changed.emit("VoxOS: Thinking...", "#A855F7")
        try:
            from voxos.agents.listener.agent import ListenerAgent
            from voxos.agents.strategist.agent import StrategistAgent
            from voxos.agents.executor.agent import ExecutorAgent
            from voxos.agents.overseer.agent import OverseerAgent
            from voxos.core.task_manager.manager import TaskManager
            from voxos.core.permissions import PermissionManager
            
            listener = ListenerAgent()
            strategist = StrategistAgent()
            executor = ExecutorAgent()
            overseer = OverseerAgent()
            manager = TaskManager()
            permissions = PermissionManager()
            
            task = await manager.submit_task(
                user_request=text,
                session_id=self.session_id
            )
            
            result_task = await manager.run_pipeline(
                task=task,
                listener=listener,
                strategist=strategist,
                executor=executor,
                overseer=overseer,
                permissions=permissions
            )
            
            if result_task.result:
                self.transcript_received.emit(f"VoxOS [Success]: {result_task.result}")
            elif result_task.error_info:
                self.transcript_received.emit(f"VoxOS [Error]: {result_task.error_info}")
            else:
                self.transcript_received.emit(f"VoxOS: Completed with status {result_task.status.name}")
        except Exception as e:
            log.exception("chat_task_error", error=str(e))
            self.transcript_received.emit(f"VoxOS [System Error]: {str(e)}")
        finally:
            self.status_changed.emit("VoxOS: Idle", "#94a3b8")

    def quit(self):
        log.info("Shutting down VoxOS...")
        self.hotkey_listener.stop()
        if self._voice_task:
            self._voice_task.cancel()
        self.stop_backend()
        self.app.quit()


def run_ui(session_id: str):
    """Entry point for the PySide6 UI."""
    if qasync is None:
        raise ImportError("qasync is required for VoxOS UI. Install with `pip install qasync`.")
        
    app = QApplication.instance() or QApplication(sys.argv)
    
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    voxos = JarvisApp(session_id=session_id)
    print("VoxOS initialization complete. Tray icon created.")
    
    # Force the settings window to show up immediately so we know it's alive!
    print("Forcing settings window to show...")
    voxos.show_settings()
    voxos.show_dashboard()
    
    print("Entering Qt event loop...")
    with loop:
        loop.run_forever()

