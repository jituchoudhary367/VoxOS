"""
Wake Word detection using openwakeword.
"""
import threading
import time
from typing import Callable
import numpy as np

try:
    import pyaudio
    import openwakeword
    from openwakeword.model import Model
except ImportError:
    pyaudio = None
    openwakeword = None
    Model = None

from voxos.observability.logger import get_logger
from voxos.voice.wakeword.base import WakeWordDetector

log = get_logger(__name__)

class OpenWakeWordEngine(WakeWordDetector):
    """
    Local Wake Word engine using PyAudio and openwakeword.
    Defaults to the pre-trained "hey_jarvis" or standard model.
    """
    
    def __init__(self, callback: Callable[[], None], sensitivity: float = 0.5):
        super().__init__(callback, sensitivity)
        if openwakeword is None:
            raise ImportError("openwakeword or pyaudio is not installed.")
            
        openwakeword.utils.download_models()
        # We use a default pre-trained model. 
        # Alternatively, "hey_jarvis_v0.1" can be used if available.
        self.oww_model = Model(inference_framework="onnx")
        
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000
        self.CHUNK = 1280
        
        self.audio = pyaudio.PyAudio()
        self.stream = None
        
        self._stop_event = threading.Event()
        self._thread = None
        self._paused = False

    def start(self):
        if self.is_listening:
            return
            
        log.info("Starting OpenWakeWord Engine...")
        self.stream = self.audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK
        )
        self.is_listening = True
        self._stop_event.clear()
        self._paused = False
        
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def _listen_loop(self):
        log.info("Listening for wake word...")
        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(0.1)
                continue
                
            try:
                data = self.stream.read(self.CHUNK, exception_on_overflow=False)
                # Convert bytes to numpy array
                audio_data = np.frombuffer(data, dtype=np.int16)
                
                # Predict
                prediction = self.oww_model.predict(audio_data)
                
                # Check threshold (usually dictionary of model names -> scores)
                for mdl_name, score in prediction.items():
                    if score >= self.sensitivity:
                        log.info("wake_word_detected", model=mdl_name, score=score)
                        self.callback()
                        # Cool down to avoid multiple triggers
                        time.sleep(1.0)
                        break
            except Exception as e:
                log.error("wakeword_audio_error", error=str(e))
                time.sleep(0.1)

    def pause(self):
        log.info("Pausing wake word detection...")
        self._paused = True

    def resume(self):
        log.info("Resuming wake word detection...")
        self._paused = False

    def stop(self):
        log.info("Stopping Wake Word Engine...")
        self.is_listening = False
        self._stop_event.set()
        
        if self._thread:
            self._thread.join(timeout=2.0)
            
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
