"""Shared harness for the VoxOS research experiments.

Every experiment runs the real agents, the real tool registry and the real
LLM provider configured in `.env`. The harness only adds instrumentation:

* LLM call/token counters wrapped around the provider's `complete()`
* an auto-approver that stands in for the human clicking "Approve"
* a sandbox workspace so file tasks never touch real user folders
* database sync disabled, so latency measures the agents, not Postgres writes
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "benchmarks" / "results"
# Kept outside the repo on purpose: the Listener's system-info regex matches
# the substring "os" anywhere, so a path like D:\voxos\... would reroute every
# file request and measure that bug instead of the file tools.
WORKSPACE = Path.home() / "VoxBench"

# Must be set before voxos.config is imported anywhere.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
os.environ["VOX_WORKSPACE_ROOT"] = str(WORKSPACE)
os.environ.pop("JARVIS_WORKSPACE_ROOT", None)
os.environ.setdefault("LOG_LEVEL", "ERROR")
sys.path.insert(0, str(ROOT))


def quiet_logs() -> None:
    import structlog

    logging.basicConfig(level=logging.CRITICAL)
    for name in ("voxos", "httpx", "openai", "asyncio"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
    )


quiet_logs()


# ─────────────────────────────────────────────────────────────────────────────
# LLM instrumentation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMCounter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    rate_limit_wait_ms: float = 0.0
    rate_limited: int = 0
    models: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "rate_limit_wait_ms": round(self.rate_limit_wait_ms, 1),
            "rate_limited": self.rate_limited,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "llm_latency_ms": round(self.latency_ms, 1),
            "models": sorted(set(self.models)),
        }


COUNTER = LLMCounter()


def reset_counter() -> LLMCounter:
    global COUNTER
    COUNTER.__init__()
    return COUNTER


def instrument_llm() -> None:
    """Wrap the router's provider so every completion is counted."""
    from voxos.llm.router import get_llm_router

    provider = get_llm_router().provider
    if getattr(provider, "_bench_wrapped", False):
        return
    original = provider.complete

    async def counted(messages, config):
        t0 = time.perf_counter()
        # Groq free tier rate-limits bursts; retry 429s so a throttle is not
        # scored as an agent failure. Wait time is excluded from latency.
        for attempt in range(6):
            try:
                t0 = time.perf_counter()
                resp = await original(messages, config)
                break
            except Exception as e:  # noqa: BLE001
                if "429" in str(e) or "rate" in str(e).lower():
                    w0 = time.perf_counter()
                    await asyncio.sleep(10 * (attempt + 1))
                    COUNTER.rate_limit_wait_ms += (time.perf_counter() - w0) * 1000
                    COUNTER.rate_limited += 1
                    continue
                raise
        else:
            raise RuntimeError("LLM rate limit persisted after retries")
        COUNTER.latency_ms += (time.perf_counter() - t0) * 1000
        COUNTER.calls += 1
        COUNTER.prompt_tokens += resp.prompt_tokens
        COUNTER.completion_tokens += resp.completion_tokens
        COUNTER.total_tokens += resp.total_tokens
        COUNTER.models.append(resp.model or config.model)
        return resp

    provider.complete = counted
    provider._bench_wrapped = True

    # The provider retries 429s internally (tenacity) and then falls back to a
    # local Ollama. Count throttled HTTP calls at the client so runs that were
    # delayed by the rate limiter can be excluded from latency statistics.
    primary = getattr(provider, "primary", None)
    client = getattr(primary, "_client", None)
    if client is not None:
        # One entry per API key: GROQ_API_KEY (the app's own client) plus an
        # optional GROQ_API_KEY_2. Same model, same endpoint.
        creates = [client.chat.completions.create]
        second_key = os.environ.get("GROQ_API_KEY_2")
        if second_key:
            from openai import AsyncOpenAI

            second = AsyncOpenAI(base_url=str(client.base_url), api_key=second_key, max_retries=0)
            creates.append(second.chat.completions.create)
        blocked_until = [0.0] * len(creates)

        async def create(*args, **kwargs):
            # Wait out 429s here, so the provider's fallback (a local Ollama
            # that is not installed) never fires and a throttle is not scored
            # as an agent failure. A throttled key is parked until its
            # retry-after; another key is used meanwhile. Any wait is recorded
            # and subtracted from reported latencies.
            for _ in range(40):
                now = time.monotonic()
                i = min(range(len(creates)), key=lambda k: blocked_until[k])
                if blocked_until[i] > now:
                    w0 = time.perf_counter()
                    await asyncio.sleep(blocked_until[i] - now)
                    COUNTER.rate_limit_wait_ms += (time.perf_counter() - w0) * 1000
                try:
                    return await creates[i](*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    if not ("429" in str(e) or type(e).__name__ == "RateLimitError"):
                        raise
                    COUNTER.rate_limited += 1
                    blocked_until[i] = time.monotonic() + _retry_after_seconds(e)
            raise RuntimeError("all API keys stayed rate-limited")

        client.chat.completions.create = create


def _retry_after_seconds(error: Exception) -> float:
    import re

    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return float(header) + 0.5
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(error))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2)) + 0.5
    return 10.0


async def pace(seconds: float) -> None:
    """Spacing between LLM-backed runs to stay under the provider's rate limit."""
    await asyncio.sleep(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Environment control
# ─────────────────────────────────────────────────────────────────────────────

def disable_db_sync() -> None:
    from voxos.core.task_manager.manager import TaskManager

    async def _noop(self, task):  # noqa: ARG001
        return None

    TaskManager._sync_task_to_db = _noop


def reset_workspace() -> Path:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return WORKSPACE


class AutoApprover:
    """Approves pending HIGH-risk requests, standing in for the user."""

    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.seen: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        from voxos.core.risk_engine.engine import get_risk_engine

        engine = get_risk_engine()
        while True:
            for req in engine.get_pending_approvals():
                self.seen.append({"tool": req.tool_name, "args": req.arguments})
                await engine.process_approval_response(req.id, self.approve)
            await asyncio.sleep(0.05)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def build_agents():
    from voxos.agents.executor.agent import ExecutorAgent
    from voxos.agents.listener.agent import ListenerAgent
    from voxos.agents.overseer.agent import OverseerAgent
    from voxos.agents.strategist.agent import StrategistAgent
    return ListenerAgent(), StrategistAgent(), ExecutorAgent(), OverseerAgent()


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def environment_info() -> dict[str, Any]:
    import psutil

    from voxos.config import get_settings

    cpu = platform.processor()
    try:
        import subprocess

        cpu = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip() or cpu
    except Exception:  # noqa: BLE001
        pass
    llm = get_settings().llm
    return {
        "os": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "processor": cpu,
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
        "llm_provider": llm.provider,
        "llm_fast_model": llm.fast_model,
        "llm_strong_model": llm.strong_model,
    }


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 1),
        "median": round(statistics.median(values), 1),
        "stdev": round(statistics.stdev(values), 1) if len(values) > 1 else 0.0,
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


def save(name: str, payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def pct(num: float, den: float) -> float:
    return round(100.0 * num / den, 1) if den else 0.0
