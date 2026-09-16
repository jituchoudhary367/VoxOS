"""
Pipecat Voice Pipeline.

Streams local microphone audio through VAD → STT → turn detection → the VoxOS
agent bridge, and plays the reply back.

Pipecat 1.x moved voice-activity detection and turn taking out of the transport
and into explicit pipeline processors. Passing `vad_analyzer=` to
`LocalAudioTransportParams` — as this module used to — is silently ignored,
which left the pipeline with no VAD, no `UserStoppedSpeakingFrame`, and
therefore no way to finalize an utterance into a task. The processor chain is
built explicitly here and covered by tests/unit/test_voice_pipeline_build.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_start import (
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from voxos.config import STTSettings, TTSSettings, VoiceSettings
from voxos.observability.logger import get_logger
from voxos.voice.bridge import VoxBridge
from voxos.voice.gate import MicGate, MicGateProcessor

log = get_logger(__name__)

# VAD defaults. `confidence` and `min_volume` are the two knobs that decide
# whether room noise is treated as speech; both are deliberately stricter than
# Pipecat's defaults because VoxOS runs on an open desktop mic, not a headset.
DEFAULT_VAD_CONFIDENCE = 0.8
DEFAULT_VAD_START_SECS = 0.2
DEFAULT_VAD_STOP_SECS = 0.4
DEFAULT_VAD_MIN_VOLUME = 0.65

# How long to wait after the user pauses before answering. This is the single
# biggest contributor to perceived response latency.
DEFAULT_USER_SPEECH_TIMEOUT = 0.6


def build_stt_settings(stt_settings: STTSettings) -> DeepgramSTTService.Settings:
    """Translate VoxOS STT configuration into Deepgram live-transcription options.

    Interim results stay on: turn detection uses them to notice the user is
    still talking, which is what keeps VoxOS from answering mid-sentence.
    """
    keyterms = [term.strip() for term in stt_settings.deepgram_keyterms.split(",")]
    keyterms = [term for term in keyterms if term]

    settings = DeepgramSTTService.Settings(
        model=stt_settings.deepgram_model,
        language=stt_settings.deepgram_language,
        interim_results=True,
        smart_format=True,
        punctuate=True,
        profanity_filter=False,
    )

    # keyterm prompting is a nova-3 feature; older models reject the parameter.
    if keyterms and stt_settings.deepgram_model.startswith("nova-3"):
        settings.keyterm = keyterms

    return settings


def build_tts_service(
    tts_settings: TTSSettings,
    stt_settings: STTSettings,
    voice_settings: VoiceSettings,
) -> DeepgramTTSService | None:
    """Build the in-pipeline Deepgram voice, or None to use edge-tts only.

    Returns None when Deepgram TTS is not selected or no key is available, in
    which case every reply goes through the edge-tts fallback.
    """
    if tts_settings.tts_provider.strip().lower() != "deepgram":
        return None
    if not stt_settings.deepgram_api_key:
        log.warning("tts_streaming_disabled", reason="no_deepgram_api_key")
        return None

    return DeepgramTTSService(
        api_key=stt_settings.deepgram_api_key,
        sample_rate=voice_settings.tts_sample_rate,
        settings=DeepgramTTSService.Settings(voice=tts_settings.deepgram_tts_voice),
    )


def build_voice_processors(
    *,
    transport_input: Any,
    stt: Any,
    bridge: Any,
    transport_output: Any,
    tts: Any = None,
    mic_gate: MicGate | None = None,
    vad_confidence: float = DEFAULT_VAD_CONFIDENCE,
    vad_start_secs: float = DEFAULT_VAD_START_SECS,
    vad_stop_secs: float = DEFAULT_VAD_STOP_SECS,
    vad_min_volume: float = DEFAULT_VAD_MIN_VOLUME,
    user_speech_timeout: float = DEFAULT_USER_SPEECH_TIMEOUT,
) -> list[Any]:
    """Return the ordered processor chain for the local voice pipeline.

    `tts` sits between the bridge and the output so the bridge can speak by
    pushing a `TTSSpeakFrame` downstream: the service streams audio to the
    output transport, which plays it and broadcasts the bot-speaking frames the
    mic gate needs. Pass None to run without a streaming voice, in which case
    replies go out through the edge-tts fallback instead.

    Kept free of audio hardware so the wiring — especially "is VAD actually in
    the pipeline?" — can be asserted in unit tests.
    """
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=vad_confidence,
                start_secs=vad_start_secs,
                stop_secs=vad_stop_secs,
                min_volume=vad_min_volume,
            )
        )
    )

    turns = UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=user_speech_timeout)],
        )
    )

    chain = [
        transport_input,
        MicGateProcessor(gate=mic_gate),
        vad,
        stt,
        turns,
        bridge,
        tts,
        transport_output,
    ]
    return [processor for processor in chain if processor is not None]


class VoicePipeline:
    """Coordinates the real-time voice interaction loop using Pipecat."""

    def __init__(self, session_id: str, push_to_talk: bool = False):
        self.session_id = session_id
        self.push_to_talk = push_to_talk
        self.runner: PipelineRunner | None = None
        self.task: PipelineWorker | None = None
        self.bridge: VoxBridge | None = None
        self.mic_gate: MicGate | None = None
        self.on_transcript = None
        self.on_status = None

    def set_push_to_talk_open(self, open_mic: bool) -> None:
        """Open or close the microphone for a push-to-talk press.

        Safe to call before the pipeline has started; the gate picks up the
        latest state when it is created.
        """
        self.push_to_talk = not open_mic
        if self.mic_gate:
            self.mic_gate.set_manual_mute(not open_mic)

    async def start(self) -> None:
        """Start the voice pipeline locally."""
        from voxos.config import get_settings

        settings = get_settings()
        stt_settings = settings.stt
        voice_settings = settings.voice

        if not stt_settings.deepgram_api_key:
            log.error("stt_api_key_missing", provider=stt_settings.stt_provider)
            raise RuntimeError(
                "DEEPGRAM_API_KEY is not set. Voice input cannot start without an STT key."
            )

        log.info(
            "voice_pipeline_starting",
            session_id=self.session_id,
            stt_model=stt_settings.deepgram_model,
            stt_language=stt_settings.deepgram_language,
            sample_rate=voice_settings.sample_rate,
        )

        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=voice_settings.sample_rate,
                audio_in_channels=1,
                # Must match the TTS output rate or replies play back off-pitch.
                audio_out_sample_rate=voice_settings.tts_sample_rate,
            )
        )

        stt = DeepgramSTTService(
            api_key=stt_settings.deepgram_api_key,
            sample_rate=voice_settings.sample_rate,
            settings=build_stt_settings(stt_settings),
        )

        tts = build_tts_service(settings.tts, stt_settings, voice_settings)

        self.mic_gate = MicGate(
            hangover_secs=voice_settings.echo_hangover_secs,
            # Push-to-talk starts closed and opens only while the key is held.
            manually_muted=self.push_to_talk,
        )

        self.bridge = VoxBridge(
            session_id=self.session_id,
            mic_gate=self.mic_gate,
            streaming_tts=tts is not None,
        )
        self.bridge.on_transcript = self.on_transcript
        self.bridge.on_status = self.on_status

        pipeline = Pipeline(
            build_voice_processors(
                transport_input=transport.input(),
                stt=stt,
                bridge=self.bridge,
                tts=tts,
                transport_output=transport.output(),
                mic_gate=self.mic_gate,
                vad_confidence=voice_settings.vad_confidence,
                vad_start_secs=voice_settings.vad_start_secs,
                vad_stop_secs=voice_settings.vad_stop_secs,
                vad_min_volume=voice_settings.vad_min_volume,
                user_speech_timeout=voice_settings.user_speech_timeout,
            )
        )

        self.task = PipelineWorker(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=voice_settings.sample_rate),
            # A listening session must survive a quiet room. The default
            # 5-minute idle timeout cancels the pipeline mid-session and the
            # microphone goes dead with no visible cause.
            idle_timeout_secs=None,
        )
        self.runner = PipelineRunner(handle_sigint=False)

        await self.bridge.start()

        log.info("voice_pipeline_started", session_id=self.session_id)
        try:
            await self.runner.run(self.task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("voice_pipeline_error", error=str(e), exc_info=True)
        finally:
            await self.bridge.stop()

    async def stop(self) -> None:
        """Stop the voice pipeline."""
        if self.task:
            await self.task.cancel()
        log.info("voice_pipeline_stopped", session_id=self.session_id)
