"""
Abstract base class for Wake Word detection engines.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable


class WakeWordDetector(ABC):
    """
    Abstract interface for Wake Word detection.
    """
    
    def __init__(self, callback: Callable[[], None], sensitivity: float = 0.5):
        """
        :param callback: Function to call when wake word is detected.
        :param sensitivity: Sensitivity threshold (0.0 to 1.0).
        """
        self.callback = callback
        self.sensitivity = sensitivity
        self.is_listening = False

    @abstractmethod
    def start(self):
        """Starts the microphone and detection loop."""
        pass

    @abstractmethod
    def stop(self):
        """Stops detection and releases resources."""
        pass

    @abstractmethod
    def pause(self):
        """Temporarily halts detection."""
        pass

    @abstractmethod
    def resume(self):
        """Resumes detection."""
        pass
