"""Task and TaskStep repositories."""

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from voxos.database.models.task import Task, TaskStep
from voxos.database.repositories.base import BaseRepository

class TaskRepository(BaseRepository[Task]):
    """Repository for managing Tasks."""
    def __init__(self, session):
        super().__init__(Task, session)

    async def get_with_steps(self, task_id: uuid.UUID | str) -> Task | None:
        """Get a task with its associated steps loaded."""
        if isinstance(task_id, str):
            task_id = uuid.UUID(task_id)
            
        stmt = (
            select(self.model)
            .where(self.model.id == task_id)
            .options(selectinload(self.model.steps))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_session(self, session_id: uuid.UUID | str, limit: int = 50) -> Sequence[Task]:
        """List tasks belonging to a specific session."""
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)
            
        stmt = (
            select(self.model)
            .where(self.model.session_id == session_id)
            .order_by(self.model.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class TaskStepRepository(BaseRepository[TaskStep]):
    """Repository for managing Task Steps."""
    def __init__(self, session):
        super().__init__(TaskStep, session)

    async def list_by_task(self, task_id: uuid.UUID | str) -> Sequence[TaskStep]:
        """List steps for a specific task."""
        if isinstance(task_id, str):
            task_id = uuid.UUID(task_id)
            
        stmt = (
            select(self.model)
            .where(self.model.task_id == task_id)
            .order_by(self.model.step_order.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
