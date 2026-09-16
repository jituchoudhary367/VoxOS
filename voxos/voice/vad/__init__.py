"""
Voice activity detection.

VoxOS uses Pipecat's Silero analyzer, wired into the pipeline as a
`VADProcessor` (see `voxos.voice.pipeline.build_voice_processors`). This module
re-exports it so `voxos.voice.vad` stays the one place VAD is referenced from.

An earlier stub class lived here whose `process_audio_chunk` always returned
False — it detected no speech at all, and anything trusting it saw silence.
"""

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

__all__ = ["SileroVADAnalyzer", "VADParams", "VADState"]
