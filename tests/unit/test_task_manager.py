"""
Unit tests for TaskManager.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from voxos.core.task_manager.manager import TaskManager
from voxos.core.task_manager.models import Task, TaskStatus

@pytest.fixture
def mock_agents():
    return {
        "listener": AsyncMock(),
        "strategist": AsyncMock(),
        "executor": AsyncMock(),
        "overseer": AsyncMock()
    }

@pytest.mark.asyncio
async def test_task_manager_initialization():
    manager = TaskManager()
    assert manager._running is False
    assert len(manager._active_tasks) == 0

@pytest.mark.asyncio
async def test_submit_task():
    manager = TaskManager()
    
    # Mock database repository
    with patch("voxos.core.task_manager.manager.TaskRepository") as mock_repo_class:
        mock_repo = mock_repo_class.return_value
        mock_repo.create.return_value = Task(
            id="test-task-1",
            session_id="session-1",
            user_request="Open calculator",
            status=TaskStatus.CREATED
        )
        
        # Mock database session
        mock_db = AsyncMock()
        
        task = await manager.submit_task("Open calculator", "session-1", mock_db)
        
        assert task is not None
        assert task.id == "test-task-1"
        assert task.status == TaskStatus.CREATED
        assert task.id in manager._active_tasks

@pytest.mark.asyncio
async def test_cancel_task():
    manager = TaskManager()
    task_id = "test-task-1"
    
    # Add a mock task directly to active tasks
    manager._active_tasks[task_id] = Task(
        id=task_id,
        session_id="session-1",
        user_request="Open calculator",
        status=TaskStatus.EXECUTING
    )
    
    # Mock event bus
    with patch("voxos.core.task_manager.manager.get_event_bus") as mock_bus_getter:
        mock_bus = AsyncMock()
        mock_bus_getter.return_value = mock_bus
        
        result = await manager.cancel_task(task_id)
        
        assert result is True
        assert manager._active_tasks[task_id].status == TaskStatus.CANCELLED
        # Event should be published
        assert mock_bus.publish.called
