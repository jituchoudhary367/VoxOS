"""Synthetic instrumented tool used by the executor and recovery experiments.

`bench.work` sleeps for a given duration and records when it started and
finished, so dependency order and concurrency can be checked from timestamps.
It can be told to fail, or to fail only on its first N calls (transient fault).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from pydantic import BaseModel, Field

from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType

TIMELINE: list[dict] = []
CALLS: dict[str, int] = defaultdict(int)


class WorkArgs(BaseModel):
    label: str = Field(..., description="Name of this unit of work")
    duration_s: float = Field(0.3, description="How long the work takes")
    fail: bool = Field(False, description="Always fail")
    fail_first_n: int = Field(0, description="Fail the first N invocations of this label (transient fault)")


class BenchWorkTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="bench.work",
            description="Benchmark unit of work: sleeps duration_s seconds then returns its label.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="system",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WorkArgs

    async def _execute(self, args: WorkArgs) -> ToolResult:
        CALLS[args.label] += 1
        start = time.perf_counter()
        await asyncio.sleep(args.duration_s)
        end = time.perf_counter()
        failed = args.fail or CALLS[args.label] <= args.fail_first_n
        TIMELINE.append({"label": args.label, "start": start, "end": end, "failed": failed})
        if failed:
            return ToolResult.fail(error=f"{args.label} failed (injected fault)", error_code="TRANSIENT_ERROR")
        return ToolResult.ok(data={"label": args.label})


def register_bench_tool() -> None:
    from voxos.core.permissions.manager import TOOL_PERMISSION_MAP
    from voxos.tools.registry import get_tool_registry

    registry = get_tool_registry()
    if registry.get("bench.work") is None:
        registry.register(BenchWorkTool())
    TOOL_PERMISSION_MAP["bench.work"] = TOOL_PERMISSION_MAP["system.get_time"]


def reset_timeline() -> None:
    TIMELINE.clear()
    CALLS.clear()
