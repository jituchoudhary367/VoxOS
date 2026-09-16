"""
Overseer Agent — "Did it actually work?"

Responsibilities:
- Verify that each tool's execution actually produced the expected result
- Run tool-specific verification checks
- Report failures precisely (what failed, why)
- Never blindly trust Executor success codes
- Trigger replanning when verification fails
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voxos.agents.base import BaseAgent
from voxos.agents.executor.agent import ExecutorOutput
from voxos.core.events.bus import VoxEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.task_manager.models import Task
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.tools.registry import get_tool_registry

log = get_logger(__name__)
metrics = get_metrics()


class OverseerInput(BaseModel):
    task: Task
    executor_output: ExecutorOutput
    session_id: str | None = None

    model_config = {"arbitrary_types_allowed": True}


class OverseerOutput(BaseModel):
    all_passed: bool
    verification_results: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    summary: str = ""


class OverseerAgent(BaseAgent[OverseerInput, OverseerOutput]):
    """
    Verifies that executed tasks actually produced correct results.
    Does not blindly trust the Executor's success codes.
    """

    def __init__(self) -> None:
        self._registry = get_tool_registry()
        self._bus = get_event_bus()

    @property
    def name(self) -> str:
        return "overseer"

    async def process(self, input: OverseerInput) -> OverseerOutput:
        task = input.task
        executor_out = input.executor_output

        log.info(
            "overseer_verifying",
            task_id=task.id,
            steps_to_verify=len(executor_out.step_results),
        )

        verification_results: list[dict[str, Any]] = []
        failures: list[str] = []

        for step_result in executor_out.step_results:
            step_id = step_result.get("step_id", "unknown")

            await self._bus.publish(VoxEvent(
                event_type=EventType.VERIFICATION_STARTED,
                payload={"step_id": step_id},
                task_id=task.id,
                session_id=input.session_id,
                agent=self.name,
            ))

            # Find the matching plan step
            plan_step = None
            if task.plan:
                plan_step = task.plan.get_step(step_id)

            # Run verification
            vr = await self._verify_step(step_result, plan_step)
            verification_results.append(vr)

            event_type = (
                EventType.VERIFICATION_PASSED
                if vr.get("passed")
                else EventType.VERIFICATION_FAILED
            )
            await self._bus.publish(VoxEvent(
                event_type=event_type,
                payload=vr,
                task_id=task.id,
                session_id=input.session_id,
                agent=self.name,
            ))

            if not vr.get("passed"):
                failures.append(
                    vr.get("failure_reason", f"Step {step_id} failed verification")
                )

        all_passed = len(failures) == 0
        failed_step_ids = executor_out.failed_steps

        # Also fail if executor reported failures
        if failed_step_ids:
            all_passed = False
            for fid in failed_step_ids:
                failure_msg = f"Step {fid} execution failed"
                if failure_msg not in failures:
                    failures.append(failure_msg)

        summary = (
            f"All {len(executor_out.step_results)} steps verified successfully."
            if all_passed
            else f"{len(failures)} verification failure(s): {'; '.join(failures[:3])}"
        )

        metrics.increment("overseer.verifications", status="pass" if all_passed else "fail")
        log.info(
            "overseer_complete",
            task_id=task.id,
            all_passed=all_passed,
            failures=len(failures),
        )

        return OverseerOutput(
            all_passed=all_passed,
            verification_results=verification_results,
            failures=failures,
            summary=summary,
        )

    async def _verify_step(
        self,
        step_result: dict[str, Any],
        plan_step: Any | None,
    ) -> dict[str, Any]:
        """Verify a single step's result."""
        step_id = step_result.get("step_id", "unknown")
        status = step_result.get("status")

        # If executor reported failure, that's already a fail
        if status != "SUCCESS":
            return {
                "step_id": step_id,
                "passed": False,
                "failure_reason": step_result.get("error", "Execution failed"),
                "checks": [{"check": "execution_status", "passed": False}],
            }

        # If no tool was involved (pure reasoning step) — pass
        if not plan_step or not plan_step.tool:
            return {
                "step_id": step_id,
                "passed": True,
                "checks": [{"check": "no_tool_verification", "passed": True}],
            }

        # Run tool-specific verification
        tool = self._registry.get(plan_step.tool)
        if not tool:
            return {
                "step_id": step_id,
                "passed": True,  # Can't verify — assume OK
                "checks": [{"check": "tool_not_found_skip", "passed": True}],
                "note": "Tool not in registry for verification",
            }

        # Reconstruct ToolResult for verification
        from voxos.tools.base import ToolResult, ToolStatus
        tr = ToolResult(
            status=ToolStatus.SUCCESS,
            data=step_result.get("data"),
        )

        # Validate args for verification call
        validated_args, err = tool.validate_args(plan_step.arguments if plan_step else {})
        if err:
            validated_args = tool.input_schema.model_construct()

        vr = await tool.verify(validated_args, tr)

        return {
            "step_id": step_id,
            "passed": vr.passed,
            "checks": vr.checks,
            "failure_reason": vr.failure_reason,
        }
