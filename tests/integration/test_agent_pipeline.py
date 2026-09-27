"""
Integration tests for the Agent Pipeline in TaskManager.
"""

import pytest


@pytest.fixture(autouse=True)
def _strategist_pipeline(monkeypatch):
    """These tests cover the single-plan Strategist pipeline; the app default
    sends reasoning requests to the multi-agent graph instead."""
    monkeypatch.setenv("AGENT_ORCHESTRATOR", "pipeline")
import uuid
import asyncio

from voxos.core.task_manager.manager import TaskManager
from voxos.core.task_manager.models import Task, TaskStatus, TaskPlan
from voxos.agents.listener.agent import ListenerOutput
from voxos.agents.strategist.agent import StrategistOutput
from voxos.agents.executor.agent import ExecutorOutput
from voxos.agents.overseer.agent import OverseerOutput

class MockListener:
    async def process(self, input):
        return ListenerOutput(intent="mock_intent", entities={}, language="en")

class MockStrategist:
    async def process(self, input):
        return StrategistOutput(plan=TaskPlan(goal="mock_goal", steps=[]))

class MockExecutor:
    async def process(self, input):
        return ExecutorOutput(task_id=input.task.id, step_results=[], failed_steps=[], results={"mock": "data"})

class MockOverseer:
    def __init__(self, all_passed=True):
        self.all_passed = all_passed
        self.call_count = 0
        
    async def process(self, input):
        self.call_count += 1
        return OverseerOutput(all_passed=self.all_passed, failures=["mock_fail"] if not self.all_passed else [])

@pytest.mark.asyncio
async def test_agent_pipeline_success(monkeypatch):
    manager = TaskManager()
    
    # Mock DB sync to avoid needing a real DB in this test
    async def mock_sync(task):
        pass
    monkeypatch.setattr(manager, "_sync_task_to_db", mock_sync)
    
    session_id = str(uuid.uuid4())
    task = await manager.create_task(session_id, "do something")
    
    listener = MockListener()
    strategist = MockStrategist()
    executor = MockExecutor()
    overseer = MockOverseer(all_passed=True)
    
    completed_task = await manager.run_pipeline(task, listener, strategist, executor, overseer)
    
    assert completed_task.status == TaskStatus.COMPLETED
    assert completed_task.result == {"mock": "data"}
    assert completed_task.intent == "mock_intent"
    assert overseer.call_count == 1

@pytest.mark.asyncio
async def test_agent_pipeline_retry_and_fail(monkeypatch):
    manager = TaskManager()
    
    async def mock_sync(task):
        pass
    monkeypatch.setattr(manager, "_sync_task_to_db", mock_sync)
    
    session_id = str(uuid.uuid4())
    task = await manager.create_task(session_id, "do something that fails")
    task.max_retries = 2
    
    listener = MockListener()
    strategist = MockStrategist()
    executor = MockExecutor()
    overseer = MockOverseer(all_passed=False)
    
    failed_task = await manager.run_pipeline(task, listener, strategist, executor, overseer)
    
    assert failed_task.status == TaskStatus.FAILED
    assert failed_task.retry_count == 2
    assert overseer.call_count == 3  # Initial + 2 retries
