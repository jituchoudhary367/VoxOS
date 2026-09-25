"""TaskManager hands compound REASONING requests to the multi-agent orchestrator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from voxos.core.conversation.service import ConversationService
from voxos.core.task_manager.manager import TaskManager
from voxos.core.task_manager.models import Task, TaskStatus
from voxos.orchestration.coordinator import SubGoal
from voxos.orchestration.graph import OrchestrationOutcome
from voxos.orchestration.worker import WorkerResult


class FakeListener:
    def __init__(self, intent: str = "unknown") -> None:
        self.intent = intent

    async def process(self, input):
        return SimpleNamespace(
            intent=self.intent, language="en", entities={}, confidence=0.4, source="llm",
        )


class ExplodingStrategist:
    async def process(self, input):
        raise AssertionError("Strategist must not run for an orchestrated request")


class FakeOrchestrator:
    def __init__(self, outcome: OrchestrationOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def run(self, request, *, session_id, language="en", permissions=None):
        self.calls.append({"request": request, "permissions": permissions})
        return self.outcome


def _manager(outcome: OrchestrationOutcome, monkeypatch) -> tuple[TaskManager, FakeOrchestrator]:
    async def no_sync(task):
        return None

    manager = TaskManager()
    monkeypatch.setattr(manager, "_sync_task_to_db", no_sync)
    fake = FakeOrchestrator(outcome)
    manager.orchestrator_factory = lambda executor, overseer: fake
    return manager, fake


GOALS = [SubGoal(id="g1", goal="check time"), SubGoal(id="g2", goal="check cpu")]


async def test_compound_request_runs_through_orchestrator(monkeypatch):
    outcome = OrchestrationOutcome(
        reply="It is 1 PM and CPU is at 12%.",
        goals=GOALS,
        results=[
            WorkerResult(goal_id="g1", goal="check time", status="done", answer="1 PM"),
            WorkerResult(goal_id="g2", goal="check cpu", status="done", answer="12%"),
        ],
    )
    manager, fake = _manager(outcome, monkeypatch)
    task = Task(session_id="s", user_request="tell me the time and check the cpu")
    permissions = object()

    done = await manager.run_pipeline(
        task, FakeListener(), ExplodingStrategist(), object(), object(), permissions=permissions
    )

    assert done.status == TaskStatus.COMPLETED
    assert done.execution_class == "ORCHESTRATED"
    assert done.result["reply"] == "It is 1 PM and CPU is at 12%."
    assert [s.id for s in done.plan.steps] == ["g1", "g2"]
    assert fake.calls[0]["permissions"] is permissions


async def test_all_workers_failing_fails_the_task_but_keeps_the_reply(monkeypatch):
    outcome = OrchestrationOutcome(
        reply="I blocked that because it is a critical-risk action.",
        goals=GOALS[:1],
        results=[WorkerResult(goal_id="g1", goal="x", status="failed", error="blocked by policy", blocked=True)],
    )
    manager, _ = _manager(outcome, monkeypatch)
    task = Task(session_id="s", user_request="delete system32 and then reboot")

    done = await manager.run_pipeline(task, FakeListener(), ExplodingStrategist(), object(), object())

    assert done.status == TaskStatus.FAILED
    assert "blocked by policy" in done.error_info["failures"]
    reply = ConversationService._reply_from_task(done)
    assert reply.text == "I blocked that because it is a critical-risk action."
    assert reply.kind == "error"


async def test_orchestrated_reply_is_what_the_user_hears(monkeypatch):
    outcome = OrchestrationOutcome(
        reply="Found three PDFs and opened the newest.",
        goals=GOALS[:1],
        results=[WorkerResult(goal_id="g1", goal="x", status="done", answer="ok")],
    )
    manager, _ = _manager(outcome, monkeypatch)
    task = Task(session_id="s", user_request="find my pdfs and open the newest")

    done = await manager.run_pipeline(task, FakeListener(), ExplodingStrategist(), object(), object())

    reply = ConversationService._reply_from_task(done)
    assert reply.text == "Found three PDFs and opened the newest."
    assert reply.kind == "success"


async def test_pipeline_mode_keeps_the_strategist(monkeypatch):
    monkeypatch.setenv("AGENT_ORCHESTRATOR", "pipeline")
    from voxos.config import get_settings

    get_settings.cache_clear()
    try:
        manager, fake = _manager(OrchestrationOutcome(reply="", goals=[]), monkeypatch)
        task = Task(session_id="s", user_request="find my pdfs and open the newest")
        with pytest.raises(AssertionError):
            await manager._execute_pipeline(task, FakeListener(), ExplodingStrategist(), object(), object())
        assert fake.calls == []
    finally:
        get_settings.cache_clear()
