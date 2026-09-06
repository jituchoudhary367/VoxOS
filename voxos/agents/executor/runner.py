"""
Tool Execution Runner — the heart of the Executor Agent.

Handles:
- Tool lookup from registry
- Risk evaluation + approval waiting
- Actual tool invocation with timeout
- Structured result collection
- Audit logging of every tool call
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from voxos.core.events.bus import JarvisEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.risk_engine.engine import get_risk_engine
from voxos.core.risk_engine.approval import get_approval_gateway
from voxos.core.task_manager.models import Task, TaskStep, StepStatus
from voxos.tools.base import BaseTool, ToolResult
from voxos.tools.registry import get_tool_registry
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


class ToolRunnerError(Exception):
    """Raised when the tool runner cannot execute a step."""


class ToolRunner:
    """
    Executes a single TaskStep by:
    1. Looking up the tool in the registry
    2. Evaluating risk
    3. Waiting for approval if needed
    4. Running the tool with a timeout
    5. Emitting events
    """

    def __init__(self) -> None:
        self._registry = get_tool_registry()
        self._risk_engine = get_risk_engine()
        self._gateway = get_approval_gateway()
        self._bus = get_event_bus()

    async def run_step(
        self,
        step: TaskStep,
        task: Task,
        timeout_seconds: int = 60,
    ) -> ToolResult:
        """
        Execute a single task step.

        Returns a ToolResult with success/failure info.
        Raises ToolRunnerError for configuration errors (not execution failures).
        """
        if step.tool is None:
            # No-op step (description-only)
            return ToolResult(
                success=True,
                data={"message": f"Step completed: {step.description}"},
                tool_name="no-op",
            )

        # 1. Look up tool
        tool = self._registry.get(step.tool)
        if tool is None:
            raise ToolRunnerError(f"Tool '{step.tool}' not found in registry")

        # 2. Risk evaluation
        decision = self._risk_engine.evaluate_tool(
            tool=tool,
            arguments=step.arguments,
            task_id=task.id,
            session_id=task.session_id,
        )

        if decision.is_blocked:
            log.warning(
                "tool_blocked",
                tool=step.tool,
                reason=decision.block_reason,
                task_id=task.id,
            )
            return ToolResult(
                success=False,
                error=f"Tool blocked: {decision.block_reason}",
                tool_name=step.tool,
            )

        # 3. Handle approval if needed
        if decision.needs_approval:
            approval = await self._risk_engine.request_approval(
                tool=tool,
                arguments=step.arguments,
                decision=decision,
                task_id=task.id,
                session_id=task.session_id,
            )
            self._gateway.register(approval.id)

            approved = await self._gateway.wait_for_decision(
                approval.id,
                timeout_seconds=120,
            )

            if approved is None:
                return ToolResult(
                    success=False,
                    error="Approval timed out",
                    tool_name=step.tool,
                )
            if not approved:
                return ToolResult(
                    success=False,
                    error="Action rejected by user",
                    tool_name=step.tool,
                )

        # 4. Emit tool started event
        await self._bus.publish(JarvisEvent(
            event_type=EventType.TOOL_STARTED,
            payload={"step_id": step.id, "tool": step.tool, "args": step.arguments},
            task_id=task.id,
            session_id=task.session_id,
            tool=step.tool,
        ))

        # 5. Execute with timeout
        start = datetime.now(timezone.utc)
        try:
            result = await asyncio.wait_for(
                tool.execute(step.arguments),
                timeout=timeout_seconds,
            )
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

            await self._bus.publish(JarvisEvent(
                event_type=EventType.TOOL_COMPLETED,
                payload={
                    "step_id": step.id,
                    "tool": step.tool,
                    "success": result.success,
                    "duration_ms": duration_ms,
                },
                task_id=task.id,
                session_id=task.session_id,
                tool=step.tool,
            ))
            metrics.record_latency("tool.duration", duration_ms, tool=step.tool)
            metrics.increment("tools.executed", tool=step.tool, success=str(result.success))
            log.info(
                "tool_executed",
                tool=step.tool,
                step_id=step.id,
                success=result.success,
                duration_ms=duration_ms,
            )
            return result

        except asyncio.TimeoutError:
            log.error("tool_timeout", tool=step.tool, timeout=timeout_seconds)
            await self._bus.publish(JarvisEvent(
                event_type=EventType.TOOL_FAILED,
                payload={"step_id": step.id, "tool": step.tool, "error": "timeout"},
                task_id=task.id,
                tool=step.tool,
            ))
            return ToolResult(
                success=False,
                error=f"Tool timed out after {timeout_seconds}s",
                tool_name=step.tool,
            )
        except Exception as e:
            log.error("tool_exception", tool=step.tool, error=str(e), exc_info=True)
            await self._bus.publish(JarvisEvent(
                event_type=EventType.TOOL_FAILED,
                payload={"step_id": step.id, "tool": step.tool, "error": str(e)},
                task_id=task.id,
                tool=step.tool,
            ))
            return ToolResult(
                success=False,
                error=str(e),
                tool_name=step.tool,
            )


_runner: ToolRunner | None = None


def get_tool_runner() -> ToolRunner:
    global _runner
    if _runner is None:
        _runner = ToolRunner()
    return _runner
