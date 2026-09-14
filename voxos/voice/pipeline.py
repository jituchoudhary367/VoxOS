"""
Pipecat Voice Pipeline.

Handles WebRTC / WebSocket / Local audio streaming, VAD (Voice Activity Detection),
and coordinates STT → Agent → TTS.
"""

from __future__ import annotations

import asyncio
from typing import Optional
import os

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from dotenv import load_dotenv
load_dotenv()

try:
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.deepgram.tts import DeepgramTTSService
except ImportError:
    from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService

try:
    from pipecat.vad.silero import SileroVADAnalyzer
except ImportError:
    from pipecat.audio.vad.silero import SileroVADAnalyzer

from voxos.voice.bridge import JarvisBridge
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class VoicePipeline:
    """
    Coordinates the real-time voice interaction loop using Pipecat.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.runner: Optional[PipelineRunner] = None
        self.task: Optional[PipelineTask] = None
        self.bridge: Optional[JarvisBridge] = None
        self.on_transcript = None
        self.on_status = None

    async def start(self) -> None:
        """Start the voice pipeline locally."""
        log.info("voice_pipeline_started", session_id=self.session_id)
        
        # Check API key
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("GROQ_API_KEY"):
            log.warning("OPENAI_API_KEY / GROQ_API_KEY not set. Voice pipeline may fail.")
        
        from voxos.config import get_settings
        settings = get_settings()
        
        deepgram_key = settings.stt.deepgram_api_key
        if not deepgram_key:
            log.error("DEEPGRAM_API_KEY is missing from configuration!")
            raise RuntimeError("Missing Deepgram API Key")
        
        # 1. Transport (Local Mic & Speaker)
        transport = LocalAudioTransport(LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ))

        # 2. STT Service
        stt = DeepgramSTTService(
            api_key=deepgram_key,
            settings=DeepgramSTTService.Settings(model="nova-2", language=None)
        )

        # 3. VoxOS Bridge (our custom FrameProcessor)
        self.bridge = JarvisBridge(session_id=self.session_id)
        self.bridge.on_transcript = self.on_transcript
        self.bridge.on_status = self.on_status

        # 4. TTS Service
        tts = DeepgramTTSService(
            api_key=deepgram_key, 
            settings=DeepgramTTSService.Settings(voice="aura-asteria-en")
        )

        # 5. Build Pipeline
        pipeline = Pipeline([
            transport.input(),
            stt,
            self.bridge,
            tts,
            transport.output(),
        ])

        # 6. Create Task & Runner
        self.task = PipelineTask(pipeline)
        self.runner = PipelineRunner()

        # Start bridge background tasks
        await self.bridge.start()

        # Run pipeline blockingly (it handles its own async loop)
        try:
            await self.runner.run(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("voice_pipeline_error", error=str(e), exc_info=True)
        finally:
            await self.bridge.stop()

    async def stop(self) -> None:
        """Stop the voice pipeline."""
        if self.task:
            await self.task.cancel()
        log.info("voice_pipeline_stopped", session_id=self.session_id)
