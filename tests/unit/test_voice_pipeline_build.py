"""Regression tests for the Pipecat processor chain used by the voice pipeline.

The historical defect these guard: `LocalAudioTransportParams(vad_analyzer=...)`
is silently ignored by Pipecat 1.x, so the pipeline ran with no VAD at all and
never emitted `UserStoppedSpeakingFrame` — voice input was accepted but never
finalized into a task.
"""

from __future__ import annotations

import pytest
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_turn_processor import UserTurnProcessor


class Sentinel:
    """Stands in for transport input/output, which need real audio hardware."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture()
def chain():
    from voxos.voice.pipeline import build_voice_processors

    return build_voice_processors(
        transport_input=Sentinel("in"),
        stt=Sentinel("stt"),
        bridge=Sentinel("bridge"),
        tts=Sentinel("tts"),
        transport_output=Sentinel("out"),
    )


def _index_of(chain, name: str) -> int:
    return next(i for i, p in enumerate(chain) if getattr(p, "name", None) == name)


def test_tts_runs_between_the_bridge_and_the_transport_output(chain) -> None:
    assert _index_of(chain, "bridge") < _index_of(chain, "tts") < _index_of(chain, "out")


def test_tts_is_optional_so_the_chain_still_builds_without_it() -> None:
    from voxos.voice.pipeline import build_voice_processors

    chain = build_voice_processors(
        transport_input=Sentinel("in"),
        stt=Sentinel("stt"),
        bridge=Sentinel("bridge"),
        tts=None,
        transport_output=Sentinel("out"),
    )

    assert None not in chain
    assert _index_of(chain, "bridge") < _index_of(chain, "out")


def test_chain_starts_at_the_transport_input(chain) -> None:
    assert chain[0].name == "in"


def test_chain_ends_at_the_transport_output(chain) -> None:
    assert chain[-1].name == "out"


def test_chain_contains_exactly_one_vad_processor(chain) -> None:
    assert sum(isinstance(p, VADProcessor) for p in chain) == 1


def test_chain_contains_exactly_one_user_turn_processor(chain) -> None:
    assert sum(isinstance(p, UserTurnProcessor) for p in chain) == 1


def test_vad_runs_before_the_stt_service(chain) -> None:
    vad_index = next(i for i, p in enumerate(chain) if isinstance(p, VADProcessor))
    stt_index = next(i for i, p in enumerate(chain) if getattr(p, "name", None) == "stt")

    assert vad_index < stt_index


def test_turn_processor_runs_after_the_stt_service(chain) -> None:
    stt_index = next(i for i, p in enumerate(chain) if getattr(p, "name", None) == "stt")
    turn_index = next(i for i, p in enumerate(chain) if isinstance(p, UserTurnProcessor))

    assert stt_index < turn_index


def test_bridge_runs_after_the_turn_processor(chain) -> None:
    turn_index = next(i for i, p in enumerate(chain) if isinstance(p, UserTurnProcessor))
    bridge_index = next(i for i, p in enumerate(chain) if getattr(p, "name", None) == "bridge")

    assert turn_index < bridge_index


def test_mic_gate_runs_before_the_vad_processor(chain) -> None:
    from voxos.voice.gate import MicGateProcessor

    gate_index = next(i for i, p in enumerate(chain) if isinstance(p, MicGateProcessor))
    vad_index = next(i for i, p in enumerate(chain) if isinstance(p, VADProcessor))

    assert gate_index < vad_index


def test_vad_uses_the_configured_sensitivity() -> None:
    from voxos.voice.pipeline import build_voice_processors

    chain = build_voice_processors(
        transport_input=Sentinel("in"),
        stt=Sentinel("stt"),
        bridge=Sentinel("bridge"),
        transport_output=Sentinel("out"),
        vad_confidence=0.85,
        vad_stop_secs=0.45,
        vad_min_volume=0.7,
    )

    vad = next(p for p in chain if isinstance(p, VADProcessor))
    params = vad._vad_controller._vad_analyzer.params

    assert params.confidence == pytest.approx(0.85)
    assert params.stop_secs == pytest.approx(0.45)
    assert params.min_volume == pytest.approx(0.7)
