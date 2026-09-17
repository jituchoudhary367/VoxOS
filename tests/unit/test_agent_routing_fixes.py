"""Regressions for defects found by the research benchmark (benchmarks/METHODOLOGY.md)."""

from __future__ import annotations

import pytest

from voxos.agents.listener.agent import ListenerAgent, ListenerOutput


# ── D6: every registered tool is permission-mapped ───────────────────────────

def test_every_registered_tool_has_a_permission() -> None:
    from voxos.core.permissions.manager import TOOL_PERMISSION_MAP
    from voxos.tools.registry import get_tool_registry

    unmapped = [m.name for m in get_tool_registry().list_tools() if m.name not in TOOL_PERMISSION_MAP]
    assert unmapped == []


# ── D1: the Strategist is told each tool's argument names ────────────────────

def test_strategist_tool_catalog_lists_argument_names() -> None:
    from voxos.agents.strategist.agent import describe_tools
    from voxos.tools.registry import get_tool_registry

    catalog = describe_tools(get_tool_registry())

    assert "os.write_file(file_path: str, content: str" in catalog
    assert "os.list_dir(dir_path: str)" in catalog


# ── D3: "os" only matches as a word ──────────────────────────────────────────

@pytest.mark.parametrize("text", ["Close Notepad", "choose a photo", "post it"])
def test_words_containing_os_are_not_system_info(text: str) -> None:
    result = ListenerAgent._fast_classify(text)
    assert result is None or result.intent != "get_system_info"


@pytest.mark.parametrize("text", ["what is my os", "system info", "what operating system am I running"])
def test_system_info_still_matches(text: str) -> None:
    result = ListenerAgent._fast_classify(text)
    assert result is not None and result.intent == "get_system_info"


def test_close_notepad_is_close_application() -> None:
    result = ListenerAgent._fast_classify("Close Notepad")
    assert result is not None
    assert result.intent == "close_application"
    assert result.entities == {"window_title": "notepad"}


# ── D4: Windows paths survive classification ─────────────────────────────────

def test_list_files_keeps_windows_path() -> None:
    result = ListenerAgent._fast_classify(r"List files in C:\Users\rotho\VoxBench")
    assert result is not None
    assert result.intent == "list_files"
    assert result.entities == {"dir_path": r"C:\Users\rotho\VoxBench"}


def test_find_files_keeps_windows_path_and_extension() -> None:
    result = ListenerAgent._fast_classify(r"Find all .py files in C:\Users\rotho\VoxBench")
    assert result is not None
    assert result.intent == "search_files"
    assert result.entities["search_path"] == r"C:\Users\rotho\VoxBench"
    assert result.entities["pattern"] == "*.py"


def test_open_domain_goes_to_browser_not_app_launcher() -> None:
    result = ListenerAgent._fast_classify("Open github.com in the browser")
    assert result is not None
    assert result.intent == "open_browser"
    assert result.entities == {"url": "https://github.com"}


def test_rule_based_results_are_marked_as_rules() -> None:
    assert ListenerAgent._fast_classify("system info").source == "rules"


# ── D2: LLM-labelled fast-path intents must earn the fast path ──────────────

def test_llm_fast_intent_with_invalid_arguments_goes_to_reasoning() -> None:
    from voxos.core.router import ExecutionClass, ExecutionRouter

    route = ExecutionRouter().route(
        "write_file", 0.95, {"path": "a.txt", "content": "x"},
        source="llm", request_text="write a.txt containing x",
    )
    assert route is ExecutionClass.REASONING


def test_llm_fast_intent_for_compound_request_goes_to_reasoning() -> None:
    from voxos.core.router import ExecutionClass, ExecutionRouter

    route = ExecutionRouter().route(
        "get_system_info", 0.95, {},
        source="llm", request_text="Tell me the current time, my system information, and my RAM usage",
    )
    assert route is ExecutionClass.REASONING


def test_llm_fast_intent_with_valid_arguments_keeps_fast_path() -> None:
    from voxos.core.router import ExecutionClass, ExecutionRouter

    route = ExecutionRouter().route("get_current_time", 0.95, {}, source="llm", request_text="What time is it?")
    assert route is ExecutionClass.FAST_PATH


# ── D8: the Strategist's own shortcut must not collapse compound requests ────

@pytest.mark.asyncio
async def test_strategist_shortcut_skips_compound_requests() -> None:
    from voxos.agents.strategist.agent import StrategistAgent, StrategistInput

    agent = StrategistAgent()
    called = {}

    async def fake_llm_plan(inp):
        called["llm"] = True
        from voxos.agents.strategist.agent import StrategistOutput
        from voxos.core.task_manager.models import TaskPlan
        return StrategistOutput(plan=TaskPlan(goal="g"))

    agent._llm_plan = fake_llm_plan
    await agent._create_plan(StrategistInput(
        intent="get_system_info",
        user_request="Tell me the current time, my system information, and my RAM usage",
    ))
    assert called.get("llm") is True


# ── D5: planner mistakes are recoverable ─────────────────────────────────────

class _Listener:
    def __init__(self, output: ListenerOutput) -> None:
        self.output = output

    async def process(self, _):
        return self.output


class _Strategist:
    def __init__(self) -> None:
        self.calls: list = []

    async def process(self, inp):
        from voxos.agents.strategist.agent import StrategistOutput
        from voxos.core.task_manager.models import TaskPlan, TaskStep

        self.calls.append(inp)
        return StrategistOutput(plan=TaskPlan(goal="g", steps=[
            TaskStep(id="s1", action="a", tool="system.get_time", arguments={}),
        ]))


class _Executor:
    """Fails the first run with a planner error, then succeeds."""

    def __init__(self, first_error: str) -> None:
        self.first_error = first_error
        self.runs = 0

    async def process(self, inp):
        from voxos.agents.executor.agent import ExecutorOutput

        self.runs += 1
        step = inp.task.plan.steps[0].id
        if self.runs == 1:
            return ExecutorOutput(task_id=inp.task.id, failed_steps=[step], step_results=[
                {"step_id": step, "status": "FAILED", "error": "Argument validation failed", "error_code": self.first_error}])
        return ExecutorOutput(task_id=inp.task.id, step_results=[{"step_id": step, "status": "SUCCESS", "data": {}}])


class _Overseer:
    async def process(self, inp):
        from voxos.agents.overseer.agent import OverseerOutput

        failed = inp.executor_output.failed_steps
        return OverseerOutput(all_passed=not failed, failures=[f"Step {s} failed" for s in failed])


async def _run(listener_output: ListenerOutput, first_error: str):
    from voxos.core.task_manager.manager import TaskManager

    tm = TaskManager()

    async def _no_db(task):
        return None

    tm._sync_task_to_db = _no_db
    strategist, executor = _Strategist(), _Executor(first_error)
    task = await tm.submit_task("request", "00000000-0000-0000-0000-000000000001")
    task = await tm.run_pipeline(task, _Listener(listener_output), strategist, executor, _Overseer())
    return task, strategist, executor


@pytest.mark.asyncio
async def test_validation_error_on_reasoning_path_triggers_replanning() -> None:
    out = ListenerOutput(intent="multi_step_task", confidence=1.0)
    task, strategist, executor = await _run(out, "VALIDATION_ERROR")

    assert task.status.value == "COMPLETED"
    assert task.retry_count == 1
    assert len(strategist.calls) == 2
    assert strategist.calls[1].previous_failures


@pytest.mark.asyncio
async def test_failed_fast_path_escalates_to_strategist() -> None:
    out = ListenerOutput(intent="get_current_time", confidence=1.0, source="rules")
    task, strategist, executor = await _run(out, "VALIDATION_ERROR")

    assert task.status.value == "COMPLETED"
    assert task.escalated_from == "FAST_PATH"
    assert len(strategist.calls) == 1
    assert strategist.calls[0].previous_failures


@pytest.mark.asyncio
async def test_real_not_found_error_still_fails_fast() -> None:
    out = ListenerOutput(intent="multi_step_task", confidence=1.0)
    task, strategist, executor = await _run(out, "FILE_NOT_FOUND")

    assert task.status.value == "FAILED"
    assert executor.runs == 1
    assert len(strategist.calls) == 1
