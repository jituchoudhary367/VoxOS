import sys
import os
import asyncio
import subprocess
from typing import Optional

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, Signal

from voxos.ui.hotkey import HotkeyListener
from voxos.observability.logger import get_logger

# We need to bridge Asyncio and Qt. We'll use qasync
try:
    import qasync
except ImportError:
    qasync = None

log = get_logger(__name__)


def reply_text(reply) -> str:
    """Return only user-facing response text for the interaction dashboard."""
    return reply.text


class VoxApp(QObject):
    """
    Main application controller for Vox.
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
        
        # The wake word detector and the global hotkey hook both fire on their
        # own threads, where asyncio.get_event_loop() raises. Capture the
        # qasync loop here, on the main thread, and marshal onto it.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        if qasync:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError as e:
                log.error("event_loop_unavailable", error=str(e))

        from voxos.config import get_settings
        voice_settings = get_settings().voice

        # Hotkey Listener: tap toggles a session, hold is push-to-talk.
        self.hotkey_listener = HotkeyListener(
            hotkey=voice_settings.hotkey,
            hold_threshold=voice_settings.hotkey_hold_secs,
        )
        self.hotkey_listener.hotkey_tapped.connect(self._on_hotkey_tapped)
        self.hotkey_listener.hold_started.connect(self._on_hold_started)
        self.hotkey_listener.hotkey_released.connect(self._on_hotkey_released)
        self.hotkey_listener.start()

        # Voice Pipeline State
        self.pipeline_running = False
        self._push_to_talk = False
        self._voice_task: Optional[asyncio.Task] = None
        self.voice = None
        self.wake_word_engine = None

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

    # ─── Wake-up paths ────────────────────────────────────────────────────
    # Two ways to reach Vox, both ending in start_listening():
    #   1. Say the wake word (openwakeword, always-on, local).
    #   2. Press the hotkey — tap for a hands-free session, hold to talk.

    def toggle_voice_mode(self):
        """Start listening, or stop if already listening."""
        if self.pipeline_running:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self, push_to_talk: bool = False):
        """Open a listening session.

        Args:
            push_to_talk: True when the hotkey is being held, which starts the
                microphone muted so only the held portion is captured.
        """
        if self.pipeline_running:
            # Already listening: a hold just reopens the mic for this press.
            self._set_push_to_talk_open(True)
            return

        self.show_dashboard()
        if getattr(self, "dashboard_window", None):
            self.dashboard_window.switch_mode(1)

        self.status_changed.emit("Vox: Listening...", "#FF00FF")
        self.pipeline_running = True
        self._push_to_talk = push_to_talk
        log.info("voice_mode_activated", push_to_talk=push_to_talk)

        # Release the microphone so the voice pipeline can claim it.
        if self.wake_word_engine:
            self.wake_word_engine.pause()

        if self._loop:
            self._voice_task = self._loop.create_task(
                self.start_voice_pipeline(push_to_talk=push_to_talk)
            )
        else:
            log.error("qasync is required to run VoicePipeline with PySide6")

    def stop_listening(self):
        """Close the listening session and hand the microphone back."""
        if not self.pipeline_running:
            return

        self.status_changed.emit(self._idle_status(), "#00FFCC")
        self.pipeline_running = False
        self._push_to_talk = False
        log.info("voice_mode_deactivated")

        if self._voice_task:
            self._voice_task.cancel()
        if self.wake_word_engine:
            self.wake_word_engine.resume()

    def _idle_status(self) -> str:
        return "Vox: Wake word on" if self.wake_word_engine else "Vox: Press Ctrl+Space"

    # ─── Hotkey gestures ──────────────────────────────────────────────────

    def _on_hotkey_tapped(self):
        """A quick press toggles a hands-free session."""
        self.toggle_voice_mode()

    def _on_hold_started(self):
        """The key is down past the threshold: open the mic for this press."""
        if self.pipeline_running:
            self._set_push_to_talk_open(True)
        else:
            self.start_listening(push_to_talk=True)
            self._set_push_to_talk_open(True)

    def _on_hotkey_released(self):
        """The key came up after a hold: close the mic, keep the session.

        The turn still finalizes on its own a moment later, via the normal
        speech timeout, so the last words spoken are not lost.
        """
        self._set_push_to_talk_open(False)

    def _set_push_to_talk_open(self, open_mic: bool):
        voice = getattr(self, "voice", None)
        if voice:
            voice.set_push_to_talk_open(open_mic)

    # ─── Wake word ────────────────────────────────────────────────────────

    def setup_wake_word(self):
        """Initialize the wake word engine, if it is enabled and available."""
        from voxos.config import get_settings

        voice_settings = get_settings().voice
        if not voice_settings.wake_word_enabled:
            log.info("wake_word_disabled_by_config")
            self.status_changed.emit(self._idle_status(), "#00FFCC")
            return

        models = [m.strip() for m in voice_settings.wake_word_models.split(",") if m.strip()]
        try:
            from voxos.voice.wakeword import OpenWakeWordEngine

            self.wake_word_engine = OpenWakeWordEngine(
                callback=self._on_wake_word,
                sensitivity=voice_settings.wake_word_sensitivity,
                wakeword_models=models or None,
            )
            self.wake_word_engine.start()
            log.info("wake_word_engine_ready", models=models)
            self.status_changed.emit("Vox: Wake word on", "#00FFCC")
        except Exception as e:
            log.error("wake_word_engine_failed", error=str(e))
            self.status_changed.emit(
                "Wake word unavailable; press Ctrl+Space or use text chat.", "#F59E0B"
            )

    def _on_wake_word(self):
        """Callback from the wake word detector thread.

        This runs on the detector's own thread, which has no event loop of its
        own — `asyncio.get_event_loop()` here raises RuntimeError and the
        wake word silently never fires. Use the loop captured on the main
        thread at startup instead.
        """
        if self._loop:
            self._loop.call_soon_threadsafe(self.start_listening)

    async def start_voice_pipeline(self, push_to_talk: bool = False):
        """Bootstraps the pipecat voice pipeline."""
        try:
            from voxos.voice.pipeline import VoicePipeline
            self.voice = VoicePipeline(
                session_id=self.session_id, push_to_talk=push_to_talk
            )
            self.voice.on_transcript = lambda text: self.transcript_received.emit(text)
            self.voice.on_status = lambda text, color: self.status_changed.emit(text, color)
            await self.voice.start()
        except asyncio.CancelledError:
            if getattr(self, "voice", None):
                await self.voice.stop()
        except Exception as e:
            log.error("voice_pipeline_crash", error=str(e))
            self.status_changed.emit("Vox: Error", "#FF0000")
        finally:
            self.voice = None
            # Re-enable wake word listening if the pipeline ends on its own
            if self.pipeline_running:
                self.pipeline_running = False
                self.status_changed.emit(self._idle_status(), "#00FFCC")
            if self.wake_word_engine:
                self.wake_word_engine.resume()

    def _handle_chat_message(self, text: str):
        if self._loop:
            self._loop.create_task(self._run_chat_task(text))

    async def _run_chat_task(self, text: str):
        self.status_changed.emit("Vox: Thinking...", "#A855F7")
        try:
            from voxos.agents.listener.agent import ListenerAgent
            from voxos.agents.strategist.agent import StrategistAgent
            from voxos.agents.executor.agent import ExecutorAgent
            from voxos.agents.overseer.agent import OverseerAgent
            from voxos.core.conversation import ConversationService, DatabaseConversationHistory
            from voxos.core.task_manager.manager import TaskManager
            from voxos.core.permissions import PermissionManager
            
            listener = ListenerAgent()
            strategist = StrategistAgent()
            executor = ExecutorAgent()
            overseer = OverseerAgent()
            manager = TaskManager()
            permissions = PermissionManager()
            service = ConversationService(
                task_manager=manager,
                history=DatabaseConversationHistory(),
                listener=listener,
                strategist=strategist,
                executor=executor,
                overseer=overseer,
                permissions=permissions,
            )

            reply = await service.handle_text(text, self.session_id)
            self.transcript_received.emit(reply_text(reply))
        except Exception as e:
            log.exception("chat_task_error", error=str(e))
            self.transcript_received.emit(
                "I couldn’t complete that request. Please try again in a moment."
            )
        finally:
            self.status_changed.emit("Vox: Idle", "#94a3b8")

    def quit(self):
        log.info("Shutting down VoxOS...")
        self.hotkey_listener.stop()
        if self.wake_word_engine:
            self.wake_word_engine.stop()
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
    
    voxos = VoxApp(session_id=session_id)
    print("VoxOS initialization complete. Tray icon created.")
    
    # Force the settings window to show up immediately so we know it's alive!
    print("Forcing settings window to show...")
    voxos.show_settings()
    voxos.show_dashboard()
    
    print("Entering Qt event loop...")
    with loop:
        loop.run_forever()
