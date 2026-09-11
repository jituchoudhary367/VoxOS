"""
Task Manager — orchestrates the full agent pipeline for a task.

Responsibilities:
- Create and lifecycle-manage Task objects
- Coordinate Listener → Strategist → Executor → Overseer
- Handle retries and replanning
- Emit events at every state transition
- Persist state to database

This is the central orchestration component. Agents do NOT call
each other directly — the TaskManager calls them in sequence.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from voxos.core.events.bus import JarvisEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.task_manager.models import Task, TaskStatus
from voxos.database.engine import get_session_factory
from voxos.database.repositories.session_repo import SessionRepository
from voxos.database.repositories.task_repo import TaskRepository
from voxos.config import get_settings
from voxos.core.permissions.manager import PermissionManager
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


class TaskManager:
    """
    Central task orchestrator.

    Creates tasks, drives them through the agent pipeline, handles
    retries/replanning, and maintains in-memory task state.
    (Persistence to DB happens via repositories — wired in Stage 2.)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._active_tasks = self._tasks
        self._running_tasks: set[str] = set()
        self._running = False

    async def submit_task(self, user_request: str, session_id: str, db: Any = None) -> Task:
        """Compatibility wrapper for the original task-manager API."""
        task = Task(session_id=session_id, user_request=user_request)
        if db is not None:
            try:
                repo = TaskRepository(db)
                try:
                    session_value: Any = uuid.UUID(session_id)
                except ValueError:
                    session_value = session_id
                payload = {
                    "id": uuid.UUID(task.id),
                    "session_id": session_value,
                    "user_request": user_request,
                    "status": task.status.value,
                    "risk_level": task.risk_level,
                    "retry_count": task.retry_count,
                }
                created = repo.create(payload)
                if asyncio.iscoroutine(created):
                    created = await created
                if isinstance(created, Task):
                    task = created
            except Exception as e:
                if get_settings().is_production:
                    raise
                log.warning("legacy_task_submit_db_unavailable", error=str(e))
        self._tasks[task.id] = task
        return task
        
    async def _sync_task_to_db(self, task: Task) -> None:
        """Sync in-memory task to database."""
        try:
            factory = get_session_factory()
            async with factory() as session:
                session_id = uuid.UUID(task.session_id)
                session_repo = SessionRepository(session)
                if await session_repo.get(session_id) is None:
                    await session_repo.create({
                        "id": session_id,
                        "status": "active",
                        "session_metadata": {},
                    })

                repo = TaskRepository(session)
                db_task = await repo.get(task.id)
                plan_data = task.plan.model_dump() if task.plan else None

                task_dict = {
                    "session_id": session_id,
                    "user_request": task.user_request,
                    "status": task.status.value,
                    "risk_level": task.risk_level,
                    "plan": plan_data,
                    "result": task.result,
                    "error_info": task.error_info,
                    "retry_count": task.retry_count,
                    "approval_status": task.approval_status,
                }

                if not db_task:
                    task_dict["id"] = uuid.UUID(task.id)
                    await repo.create(task_dict)
                else:
                    await repo.update(task.id, task_dict)
                await session.commit()
        except Exception as e:
            if get_settings().is_production:
                raise
            log.warning("task_db_sync_unavailable", task_id=task.id, error=str(e))

    async def create_task(self, session_id: str, user_request: str, language: str = "en") -> Task:
        """Create a new task, persist it, and register it."""
        task = Task(
            session_id=session_id,
            user_request=user_request,
            language=language,
        )
        self._tasks[task.id] = task
        await self._sync_task_to_db(task)
        log.info("task_created", task_id=task.id, session_id=session_id)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        session_id: str | None = None,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[Task]:
        tasks = list(self._tasks.values())
        if session_id:
            tasks = [t for t in tasks if t.session_id == session_id]
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    async def transition(self, task: Task, new_status: TaskStatus) -> None:
        """Move a task to a new status and emit the appropriate event."""
        old_status = task.status
        task.set_status(new_status)
        
        await self._sync_task_to_db(task)

        event_map: dict[TaskStatus, EventType] = {
            TaskStatus.UNDERSTANDING: EventType.TASK_STARTED,
            TaskStatus.PLANNING: EventType.PLAN_CREATED,
            TaskStatus.EXECUTING: EventType.TASK_STARTED,
            TaskStatus.COMPLETED: EventType.TASK_COMPLETED,
            TaskStatus.FAILED: EventType.TASK_FAILED,
            TaskStatus.CANCELLED: EventType.TASK_CANCELLED,
            TaskStatus.REPLANNING: EventType.TASK_REPLANNED,
        }

        event_type = event_map.get(new_status, EventType.TASK_STARTED)
        bus = get_event_bus()
        await bus.publish(JarvisEvent(
            event_type=event_type,
            payload={
                "task_id": task.id,
                "old_status": old_status,
                "new_status": new_status,
                "user_request": task.user_request[:200],
            },
            task_id=task.id,
            session_id=task.session_id,
        ))

        log.info(
            "task_status_changed",
            task_id=task.id,
            old=old_status,
            new=new_status,
        )

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or pending task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return False
        await self.transition(task, TaskStatus.CANCELLED)
        self._running_tasks.discard(task_id)
        log.info("task_cancelled", task_id=task_id)
        return True

    async def run_pipeline(
        self,
        task: Task,
        listener: Any,
        strategist: Any,
        executor: Any,
        overseer: Any,
        permissions: PermissionManager | None = None,
    ) -> Task:
        """
        Run the full Listener → Strategist → Executor → Overseer pipeline.
        
        This is the main execution loop. Handles retries and replanning.
        """
        self._running_tasks.add(task.id)
        self._running = True
        metrics.increment("tasks.started")

        try:
            async with metrics.async_timer("task.duration", status="unknown"):
                task = await self._execute_pipeline(
                    task, listener, strategist, executor, overseer, permissions
                )

        except asyncio.CancelledError:
            await self.transition(task, TaskStatus.CANCELLED)
            log.info("task_cancelled_by_signal", task_id=task.id)
        except Exception as e:
            log.error("task_pipeline_error", task_id=task.id, error=str(e), exc_info=True)
            task.error_info = {"error": str(e), "type": type(e).__name__}
            await self.transition(task, TaskStatus.FAILED)
        finally:
            self._running_tasks.discard(task.id)
            self._running = bool(self._running_tasks)
            metrics.increment(
                "tasks.completed",
                status=task.status.value,
            )

        return task

    async def _execute_pipeline(
        self,
        task: Task,
        listener: Any,
        strategist: Any,
        executor: Any,
        overseer: Any,
        permissions: PermissionManager | None = None,
    ) -> Task:
        """Internal pipeline execution with retry loop."""
        from voxos.agents.listener.agent import ListenerInput
        from voxos.agents.strategist.agent import StrategistInput
        from voxos.agents.executor.agent import ExecutorInput
        from voxos.agents.overseer.agent import OverseerInput

        # Stage 1: Listener — understand the request
        await self.transition(task, TaskStatus.UNDERSTANDING)
        listener_output = await listener.process(ListenerInput(
            text=task.user_request,
            session_id=task.session_id,
            language=task.language,
        ))

        task.intent = listener_output.intent
        task.language = listener_output.language
        task.entities = listener_output.entities
        task.touch()

        log.info(
            "listener_output",
            task_id=task.id,
            intent=listener_output.intent,
            language=listener_output.language,
            entities=listener_output.entities,
        )

        # Stage 2: Strategist — create a plan
        await self.transition(task, TaskStatus.PLANNING)
        strat_output = await strategist.process(StrategistInput(
            intent=listener_output.intent,
            entities=listener_output.entities,
            user_request=task.user_request,
            session_id=task.session_id,
            task_id=task.id,
            language=listener_output.language,
        ))

        task.plan = strat_output.plan
        task.risk_level = strat_output.plan.estimated_risk
        task.touch()

        log.info(
            "plan_created",
            task_id=task.id,
            steps=len(task.plan.steps),
            risk=task.risk_level,
        )

        # Stage 3: Execute with retry loop
        for attempt in range(task.max_retries + 1):
            if task.status == TaskStatus.CANCELLED:
                return task

            await self.transition(task, TaskStatus.EXECUTING)

            executor_output = await executor.process(ExecutorInput(
                task=task,
                session_id=task.session_id,
                permissions=permissions,
            ))

            # Stage 4: Overseer verification
            await self.transition(task, TaskStatus.VERIFYING)
            overseer_output = await overseer.process(OverseerInput(
                task=task,
                executor_output=executor_output,
                session_id=task.session_id,
            ))

            if overseer_output.all_passed:
                task.result = executor_output.results
                await self.transition(task, TaskStatus.COMPLETED)
                log.info("task_succeeded", task_id=task.id, attempts=attempt + 1)
                return task

            # Verification failed
            if task.can_retry():
                task.increment_retry()
                await self.transition(task, TaskStatus.REPLANNING)
                log.warning(
                    "task_verification_failed_retrying",
                    task_id=task.id,
                    attempt=attempt + 1,
                    failures=overseer_output.failures,
                )

                # Ask Strategist to replan with failure context
                strat_output = await strategist.process(StrategistInput(
                    intent=listener_output.intent,
                    entities=listener_output.entities,
                    user_request=task.user_request,
                    session_id=task.session_id,
                    task_id=task.id,
                    language=listener_output.language,
                    previous_failures=overseer_output.failures,
                ))
                task.plan = strat_output.plan
                task.touch()
            else:
                break

        # Exhausted retries
        task.error_info = {
            "reason": "Max retries exceeded",
            "failures": getattr(overseer_output, "failures", []),
        }
        await self.transition(task, TaskStatus.FAILED)
        log.error("task_failed_max_retries", task_id=task.id, retries=task.retry_count)
        return task

    def stats(self) -> dict[str, int]:
        """Return task count by status."""
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts


# Singleton
_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
