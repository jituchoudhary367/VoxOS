"""The Vox mark's geometry, the icon file, and local-model configuration."""

from __future__ import annotations

import math
import struct

import pytest

from voxos.ui import brand, ollama


# ── the mark ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [7, 5, 3])
def test_bars_form_a_v_symmetric_about_the_centre(count: int) -> None:
    bars = brand.bars(count)
    assert len(bars) == count
    for left, right in zip(bars, reversed(bars)):
        assert left.cx == pytest.approx(1 - right.cx)
        assert left.cy == pytest.approx(right.cy)
    centres = [b.cy for b in bars[: count // 2 + 1]]
    assert centres == sorted(centres), "each bar sits lower towards the vertex"


@pytest.mark.parametrize("count", [7, 5, 3])
def test_mark_fits_inside_the_square_with_margin(count: int) -> None:
    for b in brand.bars(count):
        x, y, w, h = brand.bar_rect(b, 1.0, scale=1.3)
        assert 0.02 < x and x + w < 0.98
        assert 0.02 < y and y + h < 0.98


@pytest.mark.parametrize("count", [7, 5, 3])
def test_dot_never_touches_a_bar(count: int) -> None:
    cx, cy, r = brand.dot(count)
    for b in brand.bars(count):
        # nearest point of the bar's capsule to the dot centre
        half_len = b.length / 2 - b.width / 2
        ny = min(max(cy, b.cy - half_len), b.cy + half_len)
        gap = math.hypot(cx - b.cx, cy - ny) - b.width / 2 - r
        assert gap > 0.01, f"dot overlaps bar at x={b.cx:.3f}"


def test_svgs_are_well_formed_and_carry_the_title() -> None:
    import xml.etree.ElementTree as ET

    for svg in (brand.svg_mark(), brand.svg_mark(mono="#000"), brand.svg_app_icon(),
                brand.svg_app_icon(count=3), brand.svg_lockup(), brand.svg_lockup(dark=True)):
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg")
        assert "Vox" in svg


def test_ico_file_lists_every_size() -> None:
    pngs = {px: b"\x89PNG-fake-" + bytes([px % 256]) for px in brand.ICON_SIZES}
    data = brand.ico_bytes(pngs)
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(brand.ICON_SIZES))
    first = struct.unpack("<BBBBHHII", data[6:22])
    assert first[0] == 16 and data[first[7]:first[7] + 4] == b"\x89PNG"


def test_small_icons_use_fewer_bolder_bars() -> None:
    assert brand._count_for(16) == 3
    assert brand._count_for(32) == 5
    assert brand._count_for(256) == 7


def test_living_mark_is_still_at_rest() -> None:
    assert brand.animated_lengths(3.7, 0.0) == [1.0] * 7
    assert len(set(round(v, 3) for v in brand.animated_lengths(3.7, 1.0))) > 1


# ── local models ─────────────────────────────────────────────────────────────

def test_recommendation_follows_ram() -> None:
    assert ollama.recommend(16).name == "qwen3.5:4b"
    assert ollama.recommend(32).name == "qwen3.5:9b"
    assert ollama.recommend(8).name == "qwen3.5:2b"


def test_is_installed_understands_latest() -> None:
    assert ollama.is_installed("qwen3.5", ["qwen3.5:latest"])
    assert ollama.is_installed("qwen3.5:4b", ["qwen3.5:4b", "x:1"])
    assert not ollama.is_installed("qwen3.5:9b", ["qwen3.5:4b"])


def _llm(**env):
    from voxos.config import LLMSettings

    return LLMSettings(_env_file=None, **env)


def test_one_model_serves_every_job_unless_overridden() -> None:
    s = _llm(OLLAMA_MODEL="qwen3.5:4b")
    assert (s.ollama_fast_model, s.ollama_strong_model, s.ollama_vision_model) == ("qwen3.5:4b",) * 3
    s = _llm(OLLAMA_MODEL="qwen3.5:4b", OLLAMA_VISION_MODEL="qwen3-vl:8b")
    assert s.ollama_vision_model == "qwen3-vl:8b" and s.ollama_fast_model == "qwen3.5:4b"


def test_unknown_mode_falls_back_to_hybrid() -> None:
    assert _llm(mode="LOCAL").mode == "local"
    assert _llm(mode="banana").mode == "hybrid"


@pytest.mark.parametrize("mode,provider_name", [("local", "ollama"), ("hybrid", "with-fallback")])
def test_router_follows_the_mode(mode: str, provider_name: str) -> None:
    from voxos.llm.router import build_router

    s = _llm(mode=mode, OLLAMA_MODEL="qwen3.5:4b", GROQ_API_KEY="gsk_test")
    router = build_router(s)
    assert provider_name in router.provider.name
    if mode == "local":
        assert router.vision_model == "qwen3.5:4b"
        assert router.config_for().model == "qwen3.5:4b"


def test_hybrid_fallback_maps_each_cloud_model_to_its_local_job() -> None:
    from voxos.llm.router import local_model_for

    s = _llm(OLLAMA_MODEL="qwen3.5:4b", OLLAMA_VISION_MODEL="qwen3-vl:4b")
    assert local_model_for(s, s.fast_model) == "qwen3.5:4b"
    assert local_model_for(s, s.vision_model) == "qwen3-vl:4b"
    assert local_model_for(s, "something/else") == "qwen3.5:4b"


def test_thinking_tags_never_reach_agents() -> None:
    from voxos.llm.openai_compat import _THINK

    assert _THINK.sub("", "<think>plan…\nmore</think>\n{\"intent\": \"x\"}") == '{"intent": "x"}'
