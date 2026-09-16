"""
Wake word trigger selection.

openwakeword returns a score per loaded model on every audio chunk. Loading the
full pretrained bundle means "timer" and "weather" — intent models, not wake
words — are scored too, and they fire on ordinary conversation. Deciding which
scores count is kept here, separate from the audio thread, so the rule can be
tested without a microphone.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from math import isfinite
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)

# openwakeword ships six pretrained models — alexa, hey_mycroft, hey_rhasspy,
# hey_jarvis, timer, weather — and none of them is "hey vox". Until a custom
# model is trained, "hey jarvis" is the acoustic trigger that opens a Vox
# session; the agent is still only ever called Vox everywhere else.
#
# To use the real phrase, train one with openwakeword's model-training
# notebook and point VOICE_WAKE_WORD_MODELS at the resulting .onnx path —
# `load_wakeword_model` accepts file paths as well as pretrained names.
#
# The bundle's "timer" and "weather" entries are intent classifiers, not wake
# words, and score high on ordinary speech — never load them.
DEFAULT_WAKEWORD_MODELS: tuple[str, ...] = ("hey_jarvis",)

# Above openwakeword's usual 0.5 example: a desktop mic in a room with a TV
# crosses 0.5 often enough to be annoying.
DEFAULT_SENSITIVITY = 0.6


def load_wakeword_model(model_names: Sequence[str], inference_framework: str = "onnx"):
    """Load the named wake word models and report the keys they will emit.

    openwakeword keys each score by however the model was requested, so
    "hey_jarvis" and "hey_jarvis_v0.1" load the same weights under different
    names. Returning the loaded keys keeps the trigger allowlist in step with
    whatever was actually loaded.

    Returns:
        A tuple of (model, frozenset of prediction keys).

    Raises:
        Whatever openwakeword raises for an unknown model name — falling back to
        the full bundle would quietly reinstate the false triggers.
    """
    import openwakeword
    from openwakeword.model import Model

    if any(name in DEFAULT_WAKEWORD_MODELS for name in model_names):
        log.warning(
            "wakeword_placeholder_phrase",
            detail="openwakeword ships no 'hey vox' model; 'hey jarvis' is the "
            "acoustic trigger until a custom model is trained",
            models=list(model_names),
        )

    # Only pretrained names are downloadable; a custom model is already a file.
    pretrained = [name for name in model_names if name in openwakeword.MODELS]
    if pretrained:
        openwakeword.utils.download_models(model_names=pretrained)

    model = Model(wakeword_models=list(model_names), inference_framework=inference_framework)
    return model, frozenset(model.models.keys())


def select_trigger(
    scores: dict[str, Any],
    allowed_models: Collection[str],
    sensitivity: float,
) -> str | None:
    """Return the name of the model that should fire the wake word, if any.

    Args:
        scores: Model name → score, as returned by `openwakeword.Model.predict`.
        allowed_models: Models permitted to trigger. Empty means "any model",
            which matches openwakeword's own default behaviour.
        sensitivity: Inclusive score threshold.

    Returns:
        The highest-scoring eligible model above the threshold, or None.
    """
    best_name: str | None = None
    best_score = float("-inf")

    for name, raw_score in scores.items():
        if allowed_models and name not in allowed_models:
            continue

        score = _as_score(raw_score)
        if score is None or score < sensitivity:
            continue

        if score > best_score:
            best_name, best_score = name, score

    return best_name


def _as_score(value: Any) -> float | None:
    """Coerce a model output to a usable float, or None if it is not one."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if isfinite(score) else None
