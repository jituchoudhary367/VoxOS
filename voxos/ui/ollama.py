"""
Local models for the Settings page: is Ollama running, which models are
installed, which one suits this PC, pulling it with progress, and a speed
test. Qt-free so it can be tested without a display.
"""

from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

DOWNLOAD_URL = "https://ollama.com/download"
LIBRARY_URL = "https://ollama.com/library"


@dataclass(frozen=True)
class Recommendation:
    name: str
    size_gb: float
    min_ram_gb: int
    why: str


# One all-rounder per size class: each sees images, calls tools, writes code
# and has a 256K context. Checked against ollama.com/library, Sept 2026.
ALL_ROUNDERS: tuple[Recommendation, ...] = (
    Recommendation("qwen3.5:9b", 6.6, 24, "Best quality that still runs on a laptop."),
    Recommendation("qwen3.5:4b", 3.4, 12, "Sees your screen, calls tools, writes code. Light enough for 16 GB."),
    Recommendation("qwen3.5:2b", 2.7, 8, "For older or 8 GB machines; noticeably less capable."),
)


@dataclass(frozen=True)
class Hardware:
    cpu: str
    ram_gb: float
    free_gb: float

    def summary(self) -> str:
        return f"{self.cpu}  ·  {self.ram_gb:.0f} GB RAM ({self.free_gb:.1f} GB free now)"


def hardware() -> Hardware:
    import psutil

    cpu = platform.processor() or "CPU"
    try:  # Windows keeps the marketing name in the registry
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:  # noqa: BLE001 — not Windows, or no access
        pass
    mem = psutil.virtual_memory()
    return Hardware(cpu=" ".join(cpu.replace("(R)", "").replace("(TM)", "").split()),
                    ram_gb=mem.total / 2**30, free_gb=mem.available / 2**30)


def recommend(ram_gb: float) -> Recommendation:
    """The strongest all-rounder this much RAM can hold beside Windows and a browser."""
    for rec in ALL_ROUNDERS:
        if ram_gb >= rec.min_ram_gb - 0.5:
            return rec
    return ALL_ROUNDERS[-1]


@dataclass
class OllamaStatus:
    running: bool
    version: str = ""
    models: list[str] = field(default_factory=list)
    error: str = ""


async def status(base_url: str, timeout: float = 3.0) -> OllamaStatus:
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            version = (await client.get(f"{base}/api/version")).json().get("version", "")
            tags = (await client.get(f"{base}/api/tags")).json()
    except Exception as e:  # noqa: BLE001 — not installed, not running, wrong URL
        return OllamaStatus(running=False, error=type(e).__name__)
    names = sorted(m.get("name", "") for m in tags.get("models", []) if m.get("name"))
    return OllamaStatus(running=True, version=version, models=names)


def is_installed(model: str, installed: list[str]) -> bool:
    """`qwen3.5` matches `qwen3.5:latest`; tags must otherwise match exactly."""
    model = model.strip()
    wanted = model if ":" in model else f"{model}:latest"
    return wanted in installed or model in installed


async def pull(
    base_url: str,
    model: str,
    on_progress: Callable[[float, str], Awaitable[None] | None] | None = None,
) -> None:
    """Download `model`, reporting (fraction 0..1, status text). Raises on failure."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None)) as client:
        async with client.stream("POST", f"{base}/api/pull", json={"model": model, "stream": True}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(event["error"])
                total, done = event.get("total") or 0, event.get("completed") or 0
                if on_progress is not None:
                    result = on_progress(done / total if total else 0.0, str(event.get("status", "")))
                    if hasattr(result, "__await__"):
                        await result


@dataclass(frozen=True)
class SpeedTest:
    reply: str
    seconds: float          # wall time, including loading the model
    tokens_per_sec: float   # generation speed once loaded


async def speed_test(base_url: str, model: str) -> SpeedTest:
    """One short answer, thinking off, timed. The first call includes load time."""
    base = base_url.rstrip("/")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "In one short sentence: what can a desktop voice assistant do?"}],
        "stream": False,
        "think": False,
        "options": {"num_predict": 60},
    }
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as client:
        resp = await client.post(f"{base}/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
    seconds = time.perf_counter() - start
    count, duration = data.get("eval_count") or 0, data.get("eval_duration") or 0
    return SpeedTest(
        reply=(data.get("message") or {}).get("content", "").strip(),
        seconds=seconds,
        tokens_per_sec=count / (duration / 1e9) if duration else 0.0,
    )
