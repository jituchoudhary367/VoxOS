"""
Mic gate — decides when captured audio can be trusted as user speech.

Two independent reasons close it:

1. **Vox is speaking.** Vox plays through the desktop speakers while the same
   desktop microphone is still streaming to the STT service. Without a gate it
   transcribes its own reply, treats it as a new instruction, and answers
   itself — the feedback loop behind most "it hallucinated a command" reports.
2. **Push-to-talk is not held.** In hold mode the microphone is open only while
   the hotkey is down, which makes room noise and self-echo nearly impossible.

The gate has two halves so the timing rules stay testable without audio
hardware: `MicGate` is pure state, `MicGateProcessor` is the Pipecat wrapper.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voxos.observability.logger import get_logger

log = get_logger(__name__)

DEFAULT_HANGOVER_SECS = 0.4


class MicGate:
    """Tracks whether captured audio should be discarded.

    Args:
        hangover_secs: How long to keep the microphone muted *after* playback
            ends. Speakers ring and the OS audio buffer drains late, so
            unmuting exactly on the last sample still lets the tail through.
        clock: Monotonic time source, injectable for tests.
        manually_muted: Initial push-to-talk state. Hold mode starts muted and
            opens while the key is down; tap mode starts open.
    """

    def __init__(
        self,
        hangover_secs: float = DEFAULT_HANGOVER_SECS,
        clock: Callable[[], float] = time.monotonic,
        manually_muted: bool = False,
    ) -> None:
        self._hangover_secs = max(0.0, hangover_secs)
        self._clock = clock
        self._bot_speaking = False
        self._reopen_at = float("-inf")
        self._manually_muted = manually_muted

    def bot_started(self) -> None:
        """Mark the start of Vox playback."""
        self._bot_speaking = True

    def bot_stopped(self) -> None:
        """Mark the end of Vox playback and start the hangover window."""
        self._bot_speaking = False
        self._reopen_at = self._clock() + self._hangover_secs

    def set_manual_mute(self, muted: bool) -> None:
        """Open or close the microphone by hand (push-to-talk)."""
        self._manually_muted = muted

    def should_drop_audio(self) -> bool:
        """Return True while captured audio cannot be trusted as user speech."""
        if self._manually_muted:
            return True
        return self._bot_speaking or self._clock() < self._reopen_at


class MicGateProcessor(FrameProcessor):
    """Drops input audio frames while the gate is closed.

    Sits between the transport input and the VAD so neither VAD nor STT ever
    sees Vox's own voice, or anything captured outside a push-to-talk press.
    """

    def __init__(self, gate: MicGate | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gate = gate or MicGate()
        self._dropped_frames = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward frames, swallowing input audio captured while gated shut."""
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self.gate.bot_started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.gate.bot_stopped()

        if isinstance(frame, InputAudioRawFrame) and self.gate.should_drop_audio():
            self._dropped_frames += 1
            if self._dropped_frames % 100 == 0:
                log.debug("mic_gate_muting", frames=self._dropped_frames)
            return

        await self.push_frame(frame, direction)
