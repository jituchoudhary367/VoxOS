"""
Wake Word detection using openwakeword.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection

import numpy as np

try:
    import openwakeword
    import pyaudio
except ImportError:
    pyaudio = None
    openwakeword = None

from voxos.observability.logger import get_logger
from voxos.voice.wakeword.base import WakeWordDetector
from voxos.voice.wakeword.detection import (
    DEFAULT_SENSITIVITY,
    DEFAULT_WAKEWORD_MODELS,
    load_wakeword_model,
    select_trigger,
)

log = get_logger(__name__)


class OpenWakeWordEngine(WakeWordDetector):
    """
    Local Wake Word engine using PyAudio and openwakeword.

    Only the models in `wakeword_models` may fire. Loading the full pretrained
    bundle also loads openwakeword's intent models ("timer", "weather"), which
    score high on ordinary speech and produce constant false wakes.
    """

    def __init__(
        self,
        callback: Callable[[], None],
        sensitivity: float = DEFAULT_SENSITIVITY,
        wakeword_models: Collection[str] | None = None,
        cooldown_secs: float = 2.0,
    ):
        super().__init__(callback, sensitivity)
        if openwakeword is None:
            raise ImportError("openwakeword or pyaudio is not installed.")

        self.wakeword_models = list(
            wakeword_models if wakeword_models is not None else DEFAULT_WAKEWORD_MODELS
        )
        self.cooldown_secs = cooldown_secs

        # `trigger_models` is the set of prediction keys the loaded model emits;
        # anything else that appears in a prediction is ignored.
        self.oww_model, self.trigger_models = load_wakeword_model(self.wakeword_models)

        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000
        self.CHUNK = 1280

        self.audio = pyaudio.PyAudio()
        self.stream = None

        self._stop_event = threading.Event()
        self._thread = None
        self._paused = threading.Event()

    def start(self):
        if self.is_listening:
            return

        log.info("wakeword_engine_starting", models=sorted(self.trigger_models))
        self._open_stream()
        self.is_listening = True
        self._stop_event.clear()
        self._paused.clear()

        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def _open_stream(self):
        if self.stream is None:
            self.stream = self.audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK,
            )

    def _close_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                log.warning("wakeword_stream_close_failed", error=str(e))
            finally:
                self.stream = None

    def _listen_loop(self):
        log.info("wakeword_listening")
        while not self._stop_event.is_set():
            if self._paused.is_set() or self.stream is None:
                time.sleep(0.1)
                continue

            try:
                data = self.stream.read(self.CHUNK, exception_on_overflow=False)
                audio_data = np.frombuffer(data, dtype=np.int16)

                prediction = self.oww_model.predict(audio_data)
                triggered = select_trigger(prediction, self.trigger_models, self.sensitivity)

                if triggered:
                    log.info(
                        "wake_word_detected",
                        model=triggered,
                        score=float(prediction[triggered]),
                    )
                    self.callback()
                    # openwakeword keeps a rolling buffer, so the same phrase
                    # keeps scoring high for a moment after it fires.
                    self._reset_model()
                    time.sleep(self.cooldown_secs)
            except Exception as e:
                log.error("wakeword_audio_error", error=str(e))
                time.sleep(0.1)

    def pause(self):
        """Release the microphone so the voice pipeline can claim it."""
        if self._paused.is_set():
            return
        log.info("wakeword_paused")
        self._paused.set()
        self._close_stream()

    def resume(self):
        """Reclaim the microphone and resume detection."""
        if not self._paused.is_set():
            return
        log.info("wakeword_resumed")
        try:
            self._open_stream()
        except Exception as e:
            log.error("wakeword_resume_failed", error=str(e))
            return
        self._reset_model()
        self._paused.clear()

    def _reset_model(self):
        """Clear the model's rolling audio buffer, where the build supports it."""
        reset = getattr(self.oww_model, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception as e:
                log.debug("wakeword_model_reset_failed", error=str(e))

    def stop(self):
        log.info("wakeword_engine_stopping")
        self.is_listening = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        self._close_stream()
