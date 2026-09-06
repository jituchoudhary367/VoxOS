"""
VoxOS Metrics — latency tracking, counters, cost estimation.
"""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

from voxos.observability.logger import get_logger

log = get_logger(__name__)


@dataclass
class LatencyRecord:
    name: str
    duration_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Simple in-memory metrics collector."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._started_at = datetime.now(timezone.utc)

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        key = self._make_key(name, labels)
        self._counters[key] += value

    def record_latency(self, name: str, duration_ms: float, **labels: str) -> None:
        key = self._make_key(name, labels)
        self._latencies[key].append(duration_ms)
        # Keep only last 1000 samples per metric
        if len(self._latencies[key]) > 1000:
            self._latencies[key] = self._latencies[key][-1000:]

    def get_counter(self, name: str, **labels: str) -> int:
        return self._counters[self._make_key(name, labels)]

    def get_avg_latency(self, name: str, **labels: str) -> float | None:
        samples = self._latencies[self._make_key(name, labels)]
        return sum(samples) / len(samples) if samples else None

    def get_p95_latency(self, name: str, **labels: str) -> float | None:
        samples = sorted(self._latencies[self._make_key(name, labels)])
        if not samples:
            return None
        idx = int(len(samples) * 0.95)
        return samples[min(idx, len(samples) - 1)]

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all metrics."""
        latency_summary = {}
        for key, samples in self._latencies.items():
            if samples:
                sorted_s = sorted(samples)
                latency_summary[key] = {
                    "count": len(samples),
                    "avg_ms": round(sum(samples) / len(samples), 2),
                    "p95_ms": round(sorted_s[int(len(sorted_s) * 0.95)], 2),
                    "min_ms": round(sorted_s[0], 2),
                    "max_ms": round(sorted_s[-1], 2),
                }
        return {
            "counters": dict(self._counters),
            "latencies": latency_summary,
            "uptime_seconds": (datetime.now(timezone.utc) - self._started_at).total_seconds(),
        }

    @contextmanager
    def timer(self, name: str, **labels: str) -> Iterator[None]:
        """Sync context manager to time a block of code."""
        start = time.monotonic()
        try:
            yield
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            self.record_latency(name, duration_ms, **labels)

    @asynccontextmanager
    async def async_timer(self, name: str, **labels: str) -> AsyncIterator[None]:
        """Async context manager to time an async block of code."""
        start = time.monotonic()
        try:
            yield
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            self.record_latency(name, duration_ms, **labels)

    @staticmethod
    def _make_key(name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"


# Global singleton
_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _metrics
