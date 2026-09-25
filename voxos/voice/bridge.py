"""
Vox Bridge — connects the Pipecat real-time voice pipeline to the VoxOS TaskManager.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voxos.agents.listener.language import detect_language
from voxos.observability.logger import get_logger
from voxos.voice.filters import SpeechQualityGate
from voxos.voice.gate import MicGate
from voxos.voice.tts.routing import TTSRoute, choose_tts_route
from voxos.voice.turns import TurnAssembler

log = get_logger(__name__)

# Upper bound on how long to wait for streamed playback to finish before
# assuming the TTS connection stalled and reopening the microphone.
DEFAULT_PLAYBACK_TIMEOUT_SECS = 60.0


def _build_conversation_service():
    """The same boundary the desktop UI talks to, so both reply identically."""
    from voxos.agents.executor.agent import ExecutorAgent
    from voxos.agents.listener.agent import ListenerAgent
    from voxos.agents.overseer.agent import OverseerAgent
    from voxos.agents.strategist.agent import StrategistAgent
    from voxos.core.conversation import ConversationService, DatabaseConversationHistory
    from voxos.core.permissions import PermissionManager
    from voxos.core.task_manager.manager import get_task_manager

    return ConversationService(
        task_manager=get_task_manager(),
        history=DatabaseConversationHistory(),
        listener=ListenerAgent(),
        strategist=StrategistAgent(),
        executor=ExecutorAgent(),
        overseer=OverseerAgent(),
        permissions=PermissionManager(),
    )


class VoxBridge(FrameProcessor):
    """
    Listens for TranscriptionFrame from STT.
    Triggers the VoxOS agent pipeline for the transcribed text.
    Speaks the result back and mutes the microphone while doing so.
    """

    def __init__(
        self,
        session_id: str,
        mic_gate: MicGate | None = None,
        quality_gate: SpeechQualityGate | None = None,
        streaming_tts: bool = True,
        playback_timeout_secs: float = DEFAULT_PLAYBACK_TIMEOUT_SECS,
        responder: Callable[[str, str], Awaitable[str]] | None = None,
    ):
        super().__init__()
        self.session_id = session_id
        self._responder = responder
        self._conversation = None
        self.mic_gate = mic_gate
        self.quality_gate = quality_gate or self._default_quality_gate()
        self.streaming_tts = streaming_tts
        self.playback_timeout_secs = playback_timeout_secs
        self._turns = TurnAssembler(quality_gate=self.quality_gate)
        self._task_queue: asyncio.Queue[str] = asyncio.Queue()
        self._processing_task: asyncio.Task | None = None
        self._playback_finished = asyncio.Event()

        self.on_transcript: Callable[[str], None] | None = None
        self.on_status: Callable[[str, str], None] | None = None

    @staticmethod
    def _default_quality_gate() -> SpeechQualityGate:
        from voxos.config import get_settings

        voice = get_settings().voice
        return SpeechQualityGate(
            min_words=voice.min_transcript_words,
            min_confidence=voice.min_transcript_confidence,
        )

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
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._turns.start()

        elif isinstance(frame, TranscriptionFrame):
            self._turns.add_fragment(frame.text, _frame_confidence(frame))

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._finalize_utterance()

        elif isinstance(frame, BotStoppedSpeakingFrame):
            # The output transport broadcasts this once streamed playback ends.
            self._playback_finished.set()

        # Must always push frames downstream to keep pipeline flowing
        await self.push_frame(frame, direction)

    def _finalize_utterance(self) -> None:
        """Submit the buffered turn, unless it looks like noise or self-echo."""
        turn = self._turns.finish()

        if turn.rejected_because:
            log.info(
                "voice_turn_rejected",
                reason=turn.rejected_because,
                confidence=round(turn.confidence, 3),
            )
            return

        if turn.text is None:
            return

        log.info("voice_turn_finalized", text=turn.text, confidence=round(turn.confidence, 3))
        if self.on_transcript:
            self.on_transcript(f"User: {turn.text}")

        # Drop anything still queued so only the latest utterance is answered.
        while not self._task_queue.empty():
            try:
                self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._task_queue.put_nowait(turn.text)

    async def _reply_for(self, user_request: str) -> str:
        """Answer one utterance through the shared conversation boundary."""
        if self._responder is not None:
            return await self._responder(user_request, self.session_id)

        if self._conversation is None:
            self._conversation = _build_conversation_service()
        reply = await self._conversation.handle_text(user_request, self.session_id)
        return reply.text

    async def _process_queue(self):
        """Background loop to sequentially answer utterances from voice input."""
        while True:
            try:
                user_request = await self._task_queue.get()

                if self.on_status:
                    self.on_status("VoxOS: Thinking...", "#FFFF00")

                response_text = await self._reply_for(user_request)

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
                await self.speak("I ran into an error while handling that request.")

    async def speak(self, text: str):
        """Speak a reply with the microphone muted throughout.

        English streams through the Deepgram TTS service in the pipeline, so
        audio starts within a few hundred milliseconds. Hindi and Hinglish go
        to edge-tts, which is synthesized in full and played out of band.
        """
        log.info("voice_speaking", text=text)
        if self.on_transcript:
            self.on_transcript(f"Vox: {text}")

        # Teach the quality gate what our own voice sounds like as a transcript,
        # so any bleed that still reaches the STT is recognised and dropped.
        self.quality_gate.note_bot_utterance(text)

        route = choose_tts_route(text, streaming_enabled=self.streaming_tts)
        if route is TTSRoute.STREAMING:
            await self._speak_streaming(text)
        else:
            await self._speak_fallback(text)

    async def _speak_streaming(self, text: str) -> None:
        """Hand the reply to the in-pipeline TTS service and wait for playback.

        The output transport broadcasts the bot-speaking frames, so the mic
        gate closes and reopens on its own here.
        """
        self._playback_finished.clear()
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

        try:
            await asyncio.wait_for(
                self._playback_finished.wait(), timeout=self.playback_timeout_secs
            )
        except TimeoutError:
            # The gate would otherwise stay shut on a stalled TTS connection.
            log.warning("voice_playback_timeout", seconds=self.playback_timeout_secs)
            if self.mic_gate:
                self.mic_gate.bot_stopped()

    async def _speak_fallback(self, text: str) -> None:
        """Synthesize with edge-tts and play it ourselves."""
        from voxos.voice.tts.edge_tts_provider import get_tts_provider

        language = detect_language(text)
        provider = get_tts_provider()
        result = await provider.synthesize(text, language=language)

        if not result.audio_bytes:
            return

        # Nothing in the pipeline knows about this audio, so the bot-speaking
        # frames that gate the microphone have to be raised by hand.
        await self._begin_playback()
        try:
            await _play_mp3(result.audio_bytes)
        finally:
            await self._end_playback()

    async def _begin_playback(self) -> None:
        if self.mic_gate:
            self.mic_gate.bot_started()
        await self.broadcast_frame(BotStartedSpeakingFrame)

    async def _end_playback(self) -> None:
        if self.mic_gate:
            self.mic_gate.bot_stopped()
        await self.broadcast_frame(BotStoppedSpeakingFrame)


def _frame_confidence(frame: TranscriptionFrame) -> float:
    """Best-effort confidence for a transcription frame.

    Deepgram reports per-alternative confidence in the raw result; Pipecat
    passes it through untouched, and other providers may omit it entirely.
    """
    result = getattr(frame, "result", None)
    if result is None:
        return 1.0
    try:
        return float(result.channel.alternatives[0].confidence)
    except (AttributeError, IndexError, TypeError, ValueError):
        return 1.0


async def _play_mp3(audio_bytes: bytes) -> None:
    """Play MP3 bytes on the default output device and wait for completion."""
    import os
    import tempfile

    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    # A fresh file per utterance: Windows keeps the previous one locked until
    # the player that opened it is finalized.
    fd, mp3_path = tempfile.mkstemp(prefix="voxos_tts_", suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)

        player = QMediaPlayer()
        audio_output = QAudioOutput()
        player.setAudioOutput(audio_output)
        player.setSource(QUrl.fromLocalFile(mp3_path))
        player.play()

        # Give Qt a moment to leave StoppedState before polling for the end.
        await asyncio.sleep(0.1)
        while player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            await asyncio.sleep(0.05)

        player.setSource(QUrl())
    finally:
        try:
            os.unlink(mp3_path)
        except OSError:
            pass
