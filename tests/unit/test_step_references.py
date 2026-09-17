"""D9: a plan step can use an earlier step's output via {{step_id.path}}."""

from __future__ import annotations

import pytest

from voxos.agents.executor.agent import resolve_step_references


RESULTS = {
    "step_1": {"status": "SUCCESS", "data": {"results": [{"path": r"C:\data\sample.csv", "name": "sample.csv"}], "count": 1}},
}


def test_whole_value_reference_keeps_type() -> None:
    args = {"file_path": "{{step_1.results.0.path}}", "count": "{{step_1.count}}"}
    assert resolve_step_references(args, RESULTS) == {"file_path": r"C:\data\sample.csv", "count": 1}


def test_embedded_reference_is_interpolated() -> None:
    args = {"content": "found {{ step_1.results.0.name }} here"}
    assert resolve_step_references(args, RESULTS) == {"content": "found sample.csv here"}


def test_nested_structures_are_resolved() -> None:
    args = {"paths": ["{{step_1.results.0.path}}"], "meta": {"n": "{{step_1.count}}"}}
    assert resolve_step_references(args, RESULTS) == {"paths": [r"C:\data\sample.csv"], "meta": {"n": 1}}


def test_unresolvable_reference_raises() -> None:
    with pytest.raises(KeyError):
        resolve_step_references({"file_path": "{{step_1.results.5.path}}"}, RESULTS)


def test_plain_arguments_are_untouched() -> None:
    args = {"file_path": "notes.txt", "n": 3}
    assert resolve_step_references(args, RESULTS) == args


@pytest.mark.asyncio
async def test_executor_feeds_search_result_into_read() -> None:
    from voxos.agents.executor.agent import ExecutorAgent, ExecutorInput
    from voxos.core.task_manager.models import StepStatus, Task, TaskPlan, TaskStep
    from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType
    from voxos.core.permissions.manager import TOOL_PERMISSION_MAP
    from voxos.tools.registry import get_tool_registry
    from pydantic import BaseModel

    class EchoArgs(BaseModel):
        value: str

    class EchoTool(BaseTool):
        @property
        def metadata(self) -> ToolMetadata:
            return ToolMetadata(name="test.echo", description="echo", risk_level=RiskLevel.LOW,
                                requires_confirmation=False, verification_type=VerificationType.NONE,
                                timeout_seconds=5, category="system")

        @property
        def input_schema(self):
            return EchoArgs

        async def _execute(self, args: EchoArgs) -> ToolResult:
            return ToolResult.ok(data={"echo": args.value, "items": [{"path": args.value + ".csv"}]})

    registry = get_tool_registry()
    if registry.get("test.echo") is None:
        registry.register(EchoTool())
    TOOL_PERMISSION_MAP["test.echo"] = TOOL_PERMISSION_MAP["system.get_time"]

    plan = TaskPlan(goal="g", steps=[
        TaskStep(id="step_1", action="find", tool="test.echo", arguments={"value": "sample"}),
        TaskStep(id="step_2", action="use", tool="test.echo", depends_on=["step_1"],
                 arguments={"value": "{{step_1.items.0.path}}"}),
    ])
    task = Task(session_id="s", user_request="r", plan=plan)
    out = await ExecutorAgent().process(ExecutorInput(task=task))

    assert out.failed_steps == []
    assert plan.steps[1].status == StepStatus.COMPLETED
    assert plan.steps[1].arguments == {"value": "sample.csv"}
    assert out.results["step_2"]["data"]["echo"] == "sample.csv"
