"""Tasks route — task lifecycle management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from voxos.core.task_manager.manager import get_task_manager
from voxos.core.task_manager.models import TaskStatus
from voxos.observability.logger import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.get("/tasks", response_model=list[dict])
async def list_tasks(session_id: str | None = None, limit: int = 20):
    """List tasks, optionally filtered by session."""
    manager = get_task_manager()
    tasks = manager.list_tasks(session_id=session_id, limit=limit)
    return [t.to_summary() for t in tasks]


@router.get("/tasks/{task_id}", response_model=dict)
async def get_task(task_id: str):
    """Get detailed task information."""
    manager = get_task_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task.to_summary()


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(task_id: str):
    """Cancel a running or pending task."""
    manager = get_task_manager()
    cancelled = await manager.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found or already finished")


@router.get("/tasks/_stats", response_model=dict)
async def task_stats():
    """Return task counts by status."""
    manager = get_task_manager()
    return {"stats": manager.stats(), "total": sum(manager.stats().values())}
