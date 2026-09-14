"""
VoxOS Bridge - Connects the Pipecat real-time voice pipeline with the VoxOS TaskManager.
"""

import asyncio
from typing import Optional, Callable

from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, TranscriptionFrame, TextFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame

from voxos.core.task_manager.manager import get_task_manager
from voxos.api.routes.chat import _get_agents, _build_response
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class JarvisBridge(FrameProcessor):
    """
    Listens for TranscriptionFrame from STT.
    Triggers the VoxOS pipeline for the transcribed text.
    Sends TextFrame down to TTS with the result.
    """

    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
        self._task_queue: asyncio.Queue[str] = asyncio.Queue()
        self._processing_task: Optional[asyncio.Task] = None
        self._utterance_buffer: list[str] = []
        
        self.on_transcript: Optional[Callable[[str], None]] = None
        self.on_status: Optional[Callable[[str, str], None]] = None

    async def start(self):
        """Start the background task processing loop."""
        self._processing_task = asyncio.create_task(self._process_queue())

    async def stop(self):
        """Stop the background task processing loop."""
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Intercept transcription frames; pass all frames downstream."""
        # Process parent class stuff (just in case)
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            # Clear buffer on new utterance
            self._utterance_buffer.clear()
            
        elif isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                # Append fragment to the current utterance buffer
                self._utterance_buffer.append(text)
                
        elif isinstance(frame, UserStoppedSpeakingFrame):
            # User finished turn, join fragments and submit
            full_text = " ".join(self._utterance_buffer).strip()
            if full_text:
                log.info("voice_turn_finalized", text=full_text)
                if self.on_transcript:
                    self.on_transcript(f"User: {full_text}")
                
                # Clear pending queue so we only process the latest utterance
                while not self._task_queue.empty():
                    try:
                        self._task_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                await self._task_queue.put(full_text)
                self._utterance_buffer.clear()

        # Must always push frames downstream to keep pipeline flowing
        await self.push_frame(frame, direction)

    async def _process_queue(self):
        """Background loop to sequentially process VoxOS tasks from voice input."""
        listener, strategist, executor, overseer = _get_agents()
        task_manager = get_task_manager()

        while True:
            try:
                user_request = await self._task_queue.get()
                
                if self.on_status:
                    self.on_status("VoxOS: Thinking...", "#FFFF00")
                
                # Create Task
                task = await task_manager.create_task(
                    session_id=self.session_id,
                    user_request=user_request,
                )
                
                # Run the heavy pipeline
                task = await task_manager.run_pipeline(
                    task=task,
                    listener=listener,
                    strategist=strategist,
                    executor=executor,
                    overseer=overseer,
                )
                
                # Build a human-friendly response
                response_text = _build_response(task, user_request)
                
                # Speak it out
                if self.on_status:
                    self.on_status("VoxOS: Speaking...", "#00FFCC")
                await self.speak(response_text)
                
                if self.on_status:
                    self.on_status("VoxOS: Listening...", "#FF00FF")

            except asyncio.CancelledError:
                log.info("voice_bridge_stopped")
                break
            except Exception as e:
                log.error("voice_task_error", error=str(e), exc_info=True)
                await self.speak("I encountered an error while processing your request.")

    async def speak(self, text: str):
        """Push a TextFrame down to trigger the TTS service."""
        log.info("voice_speaking", text=text)
        if self.on_transcript:
            self.on_transcript(f"VoxOS: {text}")
        await self.push_frame(TextFrame(text), FrameDirection.DOWNSTREAM)
