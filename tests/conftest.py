"""
Pytest Fixtures.

Provides mock data, db sessions, and event bus mocks for testing.
"""

import pytest
import asyncio


@pytest.fixture(autouse=True)
def _private_user_data(tmp_path, monkeypatch):
    """Tests never touch the real profile/vault or learn through a real LLM."""
    monkeypatch.setenv("VOX_DATA_DIR", str(tmp_path / "vox-data"))
    monkeypatch.setenv("AGENT_LEARN_PROFILE", "false")
    import voxos.core.profile.store as profile_store

    monkeypatch.setattr(profile_store, "_profile", None)
    import voxos.core.sessions as sessions

    monkeypatch.setattr(sessions, "_store", None)
from typing import AsyncGenerator

from voxos.core.task_manager.models import Task, TaskStatus
from voxos.tools.registry import get_tool_registry

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_task() -> Task:
    """Return a default Task object for testing."""
    return Task(
        session_id="test_session",
        user_request="Get the current time",
        intent="get_current_time",
        language="en",
        status=TaskStatus.PENDING,
    )


@pytest.fixture
def tool_registry():
    """Return the populated tool registry."""
    return get_tool_registry()


@pytest.fixture
def mock_redis(monkeypatch):
    """Mock out Redis for the EventBus to prevent network calls during unit tests."""
    class MockRedis:
        async def publish(self, channel, message):
            return 1
            
        async def ping(self):
            return True
            
        async def close(self):
            pass
            
    monkeypatch.setattr("voxos.core.events.bus.aioredis.from_url", lambda url: MockRedis())
