"""
Executor Agent — "Perform the approved actions."

Responsibilities:
- Execute tool steps from the plan in correct dependency order
- Validate tool arguments before execution
- Check risk and route through ApprovalGateway when needed
- Capture structured results for each step
- NEVER invent tools — only use registered tools
- NEVER execute steps out of order (respect depends_on)
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from voxos.agents.base import BaseAgent
from voxos.core.events.bus import VoxEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.risk_engine.engine import RiskDecision, get_risk_engine
from voxos.core.task_manager.models import StepStatus, Task, TaskStep
from voxos.core.permissions.manager import PermissionManager, get_permission_manager
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.tools.base import ToolResult
from voxos.tools.registry import get_tool_registry

log = get_logger(__name__)
metrics = get_metrics()

_REFERENCE = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)((?:\.[^\s.{}]+)+)\s*\}\}")


def resolve_step_references(value: Any, results: dict[str, dict[str, Any]]) -> Any:
    """Replace `{{step_id.a.0.b}}` with that path in an earlier step's output.

    Plans are written before anything runs, so a step that needs another
    step's output ("find the file, then read it") refers to it symbolically.
    A string that is exactly one reference takes the referenced value's type;
    a reference inside longer text is interpolated as a string.

    Raises KeyError when the step, key or index does not exist.
    """
    if isinstance(value, dict):
        return {k: resolve_step_references(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_step_references(v, results) for v in value]
    if not isinstance(value, str) or "{{" not in value:
        return value

    def lookup(match: re.Match) -> Any:
        step_id, path = match.group(1), match.group(2).lstrip(".").split(".")
        if step_id not in results:
            raise KeyError(f"No output from step '{step_id}'")
        current: Any = results[step_id].get("data")
        for part in path:
            if isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise KeyError(f"'{match.group(0)}' not found in output of step '{step_id}'")
        return current

    whole = _REFERENCE.fullmatch(value.strip())
    if whole:
        return lookup(whole)
    return _REFERENCE.sub(lambda m: str(lookup(m)), value)


class ExecutorInput(BaseModel):
    task: Task
    session_id: str | None = None
    permissions: PermissionManager | None = None

    model_config = {"arbitrary_types_allowed": True}


class ExecutorOutput(BaseModel):
    task_id: str
    results: dict[str, Any] = Field(default_factory=dict)
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    failed_steps: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)


class ExecutorAgent(BaseAgent[ExecutorInput, ExecutorOutput]):
    """
    Executes the approved task plan through the Tool Registry.
    
    Security: every tool call goes through:
    1. Tool existence check
    2. Argument validation
    3. Risk engine evaluation
    4. Approval gateway (if HIGH risk)
    5. Tool execution
    6. Result capture
    """

    def __init__(self) -> None:
        self._registry = get_tool_registry()
        self._risk_engine = get_risk_engine()
        self._bus = get_event_bus()

    @property
    def name(self) -> str:
        return "executor"

    async def process(self, input: ExecutorInput) -> ExecutorOutput:
        task = input.task
        if not task.plan:
            log.error("executor_no_plan", task_id=task.id)
            return ExecutorOutput(task_id=task.id, failed_steps=["no_plan"])

        log.info("executor_started", task_id=task.id, steps=len(task.plan.steps))

        completed_ids: set[str] = set()
        all_results: dict[str, Any] = {}
        step_results: list[dict[str, Any]] = []
        failed_steps: list[str] = []
        skipped_steps: list[str] = []

        # Execute steps respecting dependency order
        remaining_steps = list(task.plan.steps)
        max_iterations = len(remaining_steps) * 2  # prevent infinite loops

        for _iter in range(max_iterations):
            if not remaining_steps:
                break

            # Find steps whose dependencies are satisfied
            ready = [
                s for s in remaining_steps
                if all(dep in completed_ids for dep in s.depends_on)
                and s.status == StepStatus.PENDING
            ]

            if not ready:
                # Check for circular dependencies or all blocked
                pending_with_unmet_deps = [
                    s for s in remaining_steps if s.status == StepStatus.PENDING
                ]
                if pending_with_unmet_deps:
                    # Failed deps block these — mark as skipped
                    for s in pending_with_unmet_deps:
                        s.status = StepStatus.SKIPPED
                        skipped_steps.append(s.id)
                break

            # Execute ready steps (could run in parallel if safe)
            results = await asyncio.gather(
                *[
                    self._execute_step(s, task, input.session_id, input.permissions, all_results)
                    for s in ready
                ],
                return_exceptions=False,
            )

            for step, result in zip(ready, results):
                step_results.append(result)
                all_results[step.id] = result

                if result.get("status") == "SUCCESS":
                    completed_ids.add(step.id)
                    remaining_steps.remove(step)
                else:
                    failed_steps.append(step.id)
                    step.status = StepStatus.FAILED
                    remaining_steps.remove(step)

                    # Mark all steps that depend on this as skipped
                    for downstream in remaining_steps:
                        if step.id in downstream.depends_on:
                            downstream.status = StepStatus.SKIPPED
                            skipped_steps.append(downstream.id)
                            remaining_steps.remove(downstream)

        log.info(
            "executor_completed",
            task_id=task.id,
            completed=len(completed_ids),
            failed=len(failed_steps),
            skipped=len(skipped_steps),
        )

        return ExecutorOutput(
            task_id=task.id,
            results=all_results,
            step_results=step_results,
            failed_steps=failed_steps,
            skipped_steps=skipped_steps,
        )

    async def _execute_step(
        self,
        step: TaskStep,
        task: Task,
        session_id: str | None,
        permissions: PermissionManager | None,
        prior_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single plan step with full security checks."""
        step.status = StepStatus.IN_PROGRESS
        step.started_at = datetime.now(timezone.utc)

        # Fill in references to earlier steps' outputs before anything checks
        # the arguments, so validation, risk and verification see real values.
        try:
            step.arguments = resolve_step_references(step.arguments, prior_results or {})
        except KeyError as e:
            step.status = StepStatus.FAILED
            step.error = f"Unresolvable step reference: {e}"
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": step.error,
                "error_code": "VALIDATION_ERROR",
            }

        await self._bus.publish(VoxEvent(
            event_type=EventType.TASK_STEP_STARTED,
            payload={"step_id": step.id, "action": step.action, "tool": step.tool},
            task_id=task.id,
            session_id=session_id,
            agent=self.name,
        ))

        # Steps without a tool — pure reasoning/formatting
        if not step.tool:
            step.status = StepStatus.COMPLETED
            step.completed_at = datetime.now(timezone.utc)
            return {"step_id": step.id, "status": "SUCCESS", "data": None, "note": "no_tool_step"}

        # 1. Look up tool
        tool = self._registry.get(step.tool)
        if not tool:
            log.error("executor_tool_not_found", tool=step.tool, step_id=step.id)
            step.status = StepStatus.FAILED
            step.error = f"Tool '{step.tool}' not found in registry"
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": step.error,
                "error_code": "TOOL_NOT_FOUND",
            }

        permission_manager = permissions or get_permission_manager()
        if not permission_manager.can_use_tool(tool.metadata.name):
            step.status = StepStatus.FAILED
            step.error = f"Permission denied for tool '{tool.metadata.name}'"
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": step.error,
                "error_code": "PERMISSION_DENIED",
            }

        # 2. Validate arguments
        validated_args, validation_error = tool.validate_args(step.arguments)
        if validation_error:
            log.error("executor_arg_validation_failed", tool=step.tool, error=validation_error)
            step.status = StepStatus.FAILED
            step.error = f"Argument validation failed: {validation_error}"
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": step.error,
                "error_code": "VALIDATION_ERROR",
            }

        # 3. Risk evaluation
        decision: RiskDecision = self._risk_engine.evaluate_tool(
            tool=tool,
            arguments=step.arguments,
            task_id=task.id,
            session_id=session_id,
        )

        await self._bus.publish(VoxEvent(
            event_type=EventType.TOOL_REQUESTED,
            payload={
                "tool": step.tool,
                "risk": decision.risk_level,
                "requires_approval": decision.requires_approval,
                "blocked": decision.is_blocked,
            },
            task_id=task.id,
            session_id=session_id,
            agent=self.name,
            tool=step.tool,
        ))

        # 4. Blocked?
        if decision.is_blocked:
            step.status = StepStatus.FAILED
            step.error = decision.block_reason or "Blocked by risk policy"
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": step.error,
                "error_code": "BLOCKED",
            }

        # 5. Approval required?
        if decision.requires_approval:
            approval = await self._risk_engine.request_approval(
                tool=tool,
                arguments=step.arguments,
                decision=decision,
                task_id=task.id,
                session_id=session_id,
            )

            # Wait for approval (with timeout)
            approved = await self._wait_for_approval(approval.id, timeout=120)
            if not approved:
                step.status = StepStatus.FAILED
                step.error = "Action rejected or timed out"
                return {
                    "step_id": step.id,
                    "status": "FAILED",
                    "error": step.error,
                    "error_code": "APPROVAL_REJECTED",
                    "approval_id": approval.id,
                }

        # 6. Execute tool
        await self._bus.publish(VoxEvent(
            event_type=EventType.TOOL_STARTED,
            payload={"tool": step.tool, "step_id": step.id},
            task_id=task.id,
            session_id=session_id,
            agent=self.name,
            tool=step.tool,
        ))

        tool_result: ToolResult = await tool.execute(validated_args)

        event_type = EventType.TOOL_COMPLETED if tool_result.success else EventType.TOOL_FAILED
        await self._bus.publish(VoxEvent(
            event_type=event_type,
            payload={
                "tool": step.tool,
                "status": tool_result.status,
                "duration_ms": tool_result.duration_ms,
            },
            task_id=task.id,
            session_id=session_id,
            agent=self.name,
            tool=step.tool,
        ))

        step.completed_at = datetime.now(timezone.utc)

        if tool_result.success:
            step.status = StepStatus.COMPLETED
            step.result = tool_result.to_dict()
            metrics.increment("executor.steps.success", tool=step.tool)
            return {"step_id": step.id, "status": "SUCCESS", "data": tool_result.data}
        else:
            step.status = StepStatus.FAILED
            step.error = tool_result.error
            metrics.increment("executor.steps.failed", tool=step.tool)
            return {
                "step_id": step.id,
                "status": "FAILED",
                "error": tool_result.error,
                "error_code": tool_result.error_code,
            }

    async def _wait_for_approval(self, approval_id: str, timeout: int = 120) -> bool:
        """
        Poll for approval status.
        In production this would use Redis pub/sub or WebSocket push.
        """
        import asyncio
        for _ in range(timeout):
            await asyncio.sleep(1)
            approval = self._risk_engine.get_approval(approval_id)
            if approval:
                if approval.status == "APPROVED":
                    return True
                if approval.status in {"REJECTED", "EXPIRED"}:
                    return False
        return False  # Timeout
