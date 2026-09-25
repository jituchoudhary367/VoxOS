"""Coordinator → parallel Workers → Synthesizer (voxos.orchestration).

The LLM is scripted per role so every decision the agents make is explicit.
Tool calls go through a fake executor/overseer with the same interface as the
real ones, which is where the risk gate lives.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from voxos.orchestration.coordinator import SubGoal, decompose
from voxos.orchestration.graph import VoxOrchestrator
from voxos.orchestration.llm import parse_json_object
from voxos.orchestration.synthesizer import synthesize
from voxos.orchestration.worker import Worker


class ScriptedLLM:
    """Answers each role from its own queue; a callable entry is called with the prompt."""

    def __init__(self, **scripts: list[Any]) -> None:
        self.scripts = {role: list(items) for role, items in scripts.items()}
        self.prompts: dict[str, list[str]] = {}

    async def ask(self, role: str, system: str, user: str, **_: Any) -> dict[str, Any]:
        self.prompts.setdefault(role, []).append(user)
        queue = self.scripts.get(role)
        if not queue:
            raise AssertionError(f"unexpected LLM call for role {role!r}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = await item(user)
        return item


class FakeExecutor:
    def __init__(self, outcomes: dict[str, dict[str, Any]] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_step(self, step, task, session_id, permissions, prior_results=None):
        self.calls.append((step.tool, dict(step.arguments)))
        outcome = self.outcomes.get(step.tool, {"status": "SUCCESS", "data": {"ok": True}})
        return {"step_id": step.id, **outcome}


class FakeOverseer:
    def __init__(self, fail_tools: set[str] | None = None) -> None:
        self.fail_tools = fail_tools or set()

    async def verify_step(self, step_result, plan_step):
        passed = step_result.get("status") == "SUCCESS" and plan_step.tool not in self.fail_tools
        return {"passed": passed, "failure_reason": None if passed else "check failed"}


def _call(tool: str, **arguments: Any) -> dict[str, Any]:
    return {"thought": f"use {tool}", "action": "tool", "tool": tool, "arguments": arguments}


def _finish(answer: str) -> dict[str, Any]:
    return {"thought": "done", "action": "finish", "answer": answer}


SATISFIED = {"satisfied": True, "missing": ""}


# ─── JSON parsing ────────────────────────────────────────────────────────────

def test_parse_json_object_accepts_fenced_and_wrapped_json():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure: {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


# ─── Coordinator ─────────────────────────────────────────────────────────────

async def test_decompose_cleans_llm_output():
    llm = ScriptedLLM(coordinator=[{
        "goals": [
            {"id": "g1", "goal": "check the time", "tools": ["system", "bogus"], "depends_on": []},
            {"id": "g2", "goal": "list downloads", "tools": ["os"], "depends_on": ["g9", "g1"]},
            {"id": "g3", "goal": "loop", "tools": ["os"], "depends_on": ["g3"]},
            {"id": "g4", "goal": "four", "tools": [], "depends_on": []},
            {"id": "g5", "goal": "five", "tools": [], "depends_on": []},
        ]
    }])

    goals = await decompose("time and downloads", memories=[], llm=llm, max_goals=4)

    assert [g.id for g in goals] == ["g1", "g2", "g3", "g4"]
    assert goals[0].tools == ["system"]
    assert goals[1].depends_on == ["g1"]
    assert goals[2].depends_on == []


async def test_decompose_falls_back_to_one_goal_when_llm_fails():
    llm = ScriptedLLM(coordinator=[ValueError("bad json")])

    goals = await decompose("do the thing", memories=[], llm=llm)

    assert len(goals) == 1
    assert goals[0].goal == "do the thing"


async def test_decompose_shows_memories_to_the_llm():
    llm = ScriptedLLM(coordinator=[{"goals": [{"id": "g1", "goal": "x", "tools": [], "depends_on": []}]}])

    await decompose("open my project folder", memories=["Project folder is D:/voxos"], llm=llm)

    assert "D:/voxos" in llm.prompts["coordinator"][0]


# ─── Worker ──────────────────────────────────────────────────────────────────

GOAL = SubGoal(id="g1", goal="what time is it", tools=["system"])


async def test_worker_acts_observes_and_finishes():
    seen_observation = {}

    async def finish_after_reading(prompt: str):
        seen_observation["text"] = prompt
        return _finish("It is 1 PM.")

    llm = ScriptedLLM(worker=[_call("system.get_time"), finish_after_reading], reflect=[SATISFIED])
    executor = FakeExecutor({"system.get_time": {"status": "SUCCESS", "data": {"time": "13:00"}}})

    result = await Worker(llm, executor, FakeOverseer()).run(GOAL, request="time?", session_id="s")

    assert result.status == "done"
    assert result.answer == "It is 1 PM."
    assert executor.calls == [("system.get_time", {})]
    assert "13:00" in seen_observation["text"]


async def test_worker_only_sees_its_tool_families():
    llm = ScriptedLLM(worker=[_finish("x")], reflect=[SATISFIED])

    await Worker(llm, FakeExecutor(), FakeOverseer()).run(GOAL, request="time?", session_id="s")

    worker = Worker(llm, FakeExecutor(), FakeOverseer())
    tools_text = worker.tools_prompt(["system"])
    assert "system.get_time" in tools_text
    assert "os.delete_file" not in tools_text


async def test_worker_stops_at_once_when_the_risk_gate_blocks():
    llm = ScriptedLLM(worker=[_call("os.shell_command", command="rm -rf /")])
    executor = FakeExecutor({
        "os.shell_command": {"status": "FAILED", "error": "blocked by policy", "error_code": "BLOCKED"},
    })

    result = await Worker(llm, executor, FakeOverseer()).run(GOAL, request="x", session_id="s")

    assert result.status == "failed"
    assert result.blocked is True
    assert "blocked by policy" in result.error
    assert len(llm.prompts["worker"]) == 1  # no second attempt to route around the block


async def test_worker_reflection_can_send_it_back_for_one_more_round():
    llm = ScriptedLLM(
        worker=[_finish("I think it's afternoon."), _call("system.get_time"), _finish("It is 13:00.")],
        reflect=[{"satisfied": False, "missing": "never checked the clock"}, SATISFIED],
    )
    executor = FakeExecutor()

    result = await Worker(llm, executor, FakeOverseer()).run(GOAL, request="time?", session_id="s")

    assert result.answer == "It is 13:00."
    assert result.reflections == 2
    assert "never checked the clock" in llm.prompts["worker"][1]


async def test_worker_gives_up_after_its_step_budget():
    llm = ScriptedLLM(
        worker=[_call("system.get_time")] * 10,
        reflect=[{"satisfied": False, "missing": "still nothing"}] * 3,
    )

    result = await Worker(llm, FakeExecutor(), FakeOverseer(), max_steps=3, extra_steps=1).run(
        GOAL, request="x", session_id="s"
    )

    assert result.status == "failed"
    assert len(result.steps) == 4


async def test_worker_reports_failed_verification_to_the_llm():
    llm = ScriptedLLM(worker=[_call("system.get_time"), _finish("ok")], reflect=[SATISFIED])

    await Worker(llm, FakeExecutor(), FakeOverseer(fail_tools={"system.get_time"})).run(
        GOAL, request="x", session_id="s"
    )

    assert "verification failed" in llm.prompts["worker"][1]


# ─── Synthesizer ─────────────────────────────────────────────────────────────

async def test_synthesize_single_result_needs_no_llm_call():
    from voxos.orchestration.worker import WorkerResult

    llm = ScriptedLLM()
    reply = await synthesize("x", [WorkerResult(goal_id="g1", goal="x", status="done", answer="All set.")], llm)

    assert reply == "All set."


async def test_synthesize_falls_back_to_joined_answers():
    from voxos.orchestration.worker import WorkerResult

    llm = ScriptedLLM(synthesizer=[ValueError("down")])
    results = [
        WorkerResult(goal_id="g1", goal="a", status="done", answer="It is 1 PM."),
        WorkerResult(goal_id="g2", goal="b", status="failed", error="no such folder"),
    ]

    reply = await synthesize("x", results, llm)

    assert "It is 1 PM." in reply
    assert "no such folder" in reply


# ─── Graph ───────────────────────────────────────────────────────────────────

def _orchestrator(llm, executor=None, memories=None) -> VoxOrchestrator:
    async def recall(session_id: str) -> list[str]:
        return list(memories or [])

    return VoxOrchestrator(
        llm=llm, executor=executor or FakeExecutor(), overseer=FakeOverseer(), recall=recall
    )


async def test_independent_goals_run_in_parallel():
    both_started = asyncio.Event()
    started: list[str] = []

    def rendezvous(answer: str):
        async def step(prompt: str):
            started.append(answer)
            if len(started) == 2:
                both_started.set()
            # Deadlocks (and times out) unless the other worker runs concurrently.
            await asyncio.wait_for(both_started.wait(), timeout=2)
            return _finish(answer)
        return step

    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "time", "tools": ["system"], "depends_on": []},
            {"id": "g2", "goal": "cpu", "tools": ["system"], "depends_on": []},
        ]}],
        worker=[rendezvous("A"), rendezvous("B")],
        reflect=[SATISFIED, SATISFIED],
        synthesizer=[{"reply": "A and B."}],
    )

    outcome = await _orchestrator(llm).run("time and cpu", session_id="s")

    assert sorted(started) == ["A", "B"]
    assert outcome.reply == "A and B."
    assert {r.status for r in outcome.results} == {"done"}


async def test_dependent_goal_sees_earlier_answer():
    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "find the biggest file", "tools": ["os"], "depends_on": []},
            {"id": "g2", "goal": "open it", "tools": ["desktop"], "depends_on": ["g1"]},
        ]}],
        worker=[_finish("Biggest is D:/big.iso"), _finish("Opened it.")],
        reflect=[SATISFIED, SATISFIED],
        synthesizer=[{"reply": "Found and opened D:/big.iso."}],
    )

    await _orchestrator(llm).run("find the biggest file and open it", session_id="s")

    assert "D:/big.iso" in llm.prompts["worker"][1]


async def test_goal_whose_dependency_failed_is_skipped():
    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "delete system32", "tools": ["os"], "depends_on": []},
            {"id": "g2", "goal": "report", "tools": [], "depends_on": ["g1"]},
        ]}],
        worker=[_call("os.delete_file", file_path="C:/Windows/System32")],
        synthesizer=[{"reply": "I blocked that."}],
    )
    executor = FakeExecutor({
        "os.delete_file": {"status": "FAILED", "error": "blocked by policy", "error_code": "BLOCKED"},
    })

    outcome = await _orchestrator(llm, executor).run("delete system32 then report", session_id="s")

    statuses = {r.goal_id: r.status for r in outcome.results}
    assert statuses == {"g1": "failed", "g2": "skipped"}
    assert outcome.blocked is True


async def test_worker_crash_becomes_a_failed_result_not_a_crashed_run():
    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "a", "tools": [], "depends_on": []},
            {"id": "g2", "goal": "b", "tools": [], "depends_on": []},
        ]}],
        worker=[RuntimeError("boom"), _finish("B done")],
        reflect=[SATISFIED],
        synthesizer=[{"reply": "B done, A failed."}],
    )

    outcome = await _orchestrator(llm).run("a and b", session_id="s")

    assert sorted(r.status for r in outcome.results) == ["done", "failed"]
    assert outcome.reply == "B done, A failed."


# ─── Routing into the orchestrator ───────────────────────────────────────────

@pytest.mark.parametrize(
    ("mode", "text", "expected"),
    [
        ("auto", "find the biggest file and open it", True),
        ("auto", "summarize my notes", False),
        ("graph", "summarize my notes", True),
        ("pipeline", "find the biggest file and open it", False),
    ],
)
def test_should_orchestrate(mode, text, expected):
    from voxos.orchestration.graph import should_orchestrate

    assert should_orchestrate(text, mode) is expected


# ─── Live-run regressions ────────────────────────────────────────────────────

def test_recovers_json_from_groq_tool_use_failed():
    import httpx
    from openai import BadRequestError

    from voxos.orchestration.llm import recover_failed_generation

    body = {"error": {
        "code": "tool_use_failed",
        "failed_generation": '{"name": "assistant", "arguments": {"action": "tool", "tool": "os.list_dir"}}',
    }}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = BadRequestError("tool_use_failed", response=httpx.Response(400, request=request), body=body)

    assert recover_failed_generation(error) == {"action": "tool", "tool": "os.list_dir"}
    assert recover_failed_generation(ValueError("x")) is None


async def test_router_json_llm_uses_recovered_generation():
    import httpx
    from openai import BadRequestError

    from voxos.llm.base import LLMConfig
    from voxos.orchestration.llm import RouterJsonLLM

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = BadRequestError(
        "tool_use_failed",
        response=httpx.Response(400, request=request),
        body={"error": {"code": "tool_use_failed", "failed_generation": '{"name": "x", "arguments": {"reply": "hi"}}'}},
    )

    class Provider:
        async def complete(self, messages, config):
            raise error

    class Router:
        provider = Provider()

        def config_for(self, *args, **kwargs):
            return LLMConfig(model="m")

    assert await RouterJsonLLM(Router()).ask("synthesizer", "s", "u") == {"reply": "hi"}


async def test_parallel_workers_see_only_their_own_goal():
    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "Get the time", "tools": ["system"], "depends_on": []},
            {"id": "g2", "goal": "Get CPU usage", "tools": ["system"], "depends_on": []},
        ]}],
        worker=[_finish("1 PM"), _finish("5%")],
        reflect=[SATISFIED, SATISFIED],
        synthesizer=[{"reply": "ok"}],
    )

    await _orchestrator(llm).run("what time is it and what's my cpu", session_id="s")

    for prompt in llm.prompts["worker"]:
        assert "what time is it and what's my cpu" not in prompt


async def test_failed_worker_reports_the_tool_error_not_the_critique():
    llm = ScriptedLLM(worker=[_call("web.search", query="groq"), _finish(""), _finish("")])
    executor = FakeExecutor({
        "web.search": {"status": "FAILED", "error": "duckduckgo-search is not installed", "error_code": "EXECUTION_ERROR"},
    })

    result = await Worker(llm, executor, FakeOverseer()).run(GOAL, request="x", session_id="s")

    assert result.status == "failed"
    assert result.error == "duckduckgo-search is not installed"
