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
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from voxos.core.request_context import user_request

from voxos.core.events.bus import VoxEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.task_manager.models import Task, TaskStatus
from voxos.agents.overseer.agent import OverseerOutput
from voxos.database.engine import get_session_factory
from voxos.database.repositories.session_repo import SessionRepository
from voxos.database.repositories.task_repo import TaskRepository
from voxos.config import get_settings
from voxos.core.permissions.manager import PermissionManager
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()

# Phase 8: Error Taxonomy & Bounded Recovery
NON_RETRYABLE_ERRORS = frozenset({
    "PERMISSION_DENIED",
    "SECURITY_BLOCKED",
    "VALIDATION_ERROR",
    "USER_CANCELLED",
    "NOT_FOUND",
    "APP_NOT_FOUND",
    "FILE_NOT_FOUND",
    "DIR_NOT_FOUND",
})

# Errors that mean the *plan* was wrong (bad argument names, unknown tool),
# not that the world refused the action. Replanning with the error text fixes
# these, so they are never fatal even though VALIDATION_ERROR is listed above
# for tool-level callers.
PLANNER_ERRORS = frozenset({
    "VALIDATION_ERROR",
    "TOOL_NOT_FOUND",
})


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
        # Builds the multi-agent orchestrator around this run's executor and
        # overseer; replaceable in tests.
        self.orchestrator_factory: Callable[[Any, Any], Any] = _default_orchestrator
        # Background persistence: one writer per task, re-run while dirty.
        self._sync_writers: dict[str, asyncio.Task] = {}
        self._sync_dirty: set[str] = set()

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
        
    def _schedule_sync(self, task: Task) -> None:
        """Persist the task's latest state without making the caller wait.

        Writes to the remote database took 2-7 s each and a task changes
        status several times, so awaiting them dominated reply latency. The
        in-memory task is the source of truth while it runs; writes for one
        task run one at a time, and changes made during a write are folded
        into a single follow-up write of the latest state.
        """
        self._sync_dirty.add(task.id)
        if task.id not in self._sync_writers:
            self._sync_writers[task.id] = asyncio.create_task(self._sync_writer(task))

    async def _sync_writer(self, task: Task) -> None:
        try:
            while task.id in self._sync_dirty:
                self._sync_dirty.discard(task.id)
                try:
                    await self._sync_task_to_db(task)
                except Exception as e:  # noqa: BLE001 — never take a task down
                    log.error("task_db_sync_failed", task_id=task.id, error=str(e))
        finally:
            self._sync_writers.pop(task.id, None)

    async def flush(self) -> None:
        """Wait for every pending background write (shutdown, tests)."""
        while self._sync_writers:
            await asyncio.gather(*list(self._sync_writers.values()), return_exceptions=True)

    async def _sync_task_to_db(self, task: Task) -> None:
        """Sync in-memory task to database."""
        try:
            factory = get_session_factory()
            async with factory() as session:
                session_id = uuid.UUID(task.session_id)
                session_repo = SessionRepository(session)
                if await session_repo.get(session_id) is None:
                    # Parallel tasks of one session race to insert this row;
                    # losing the race is fine, the row exists either way.
                    try:
                        await session_repo.create({
                            "id": session_id,
                            "status": "active",
                            "session_metadata": {},
                        })
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()

                import json
                from datetime import datetime
                
                def _json_serial(obj):
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    raise TypeError(f"Type {type(obj)} not serializable")
                
                def _to_jsonb(data):
                    if data is None:
                        return None
                    if hasattr(data, "model_dump"):
                        data = data.model_dump()
                    return json.loads(json.dumps(data, default=_json_serial))

                repo = TaskRepository(session)
                db_task = await repo.get(task.id)

                task_dict = {
                    "session_id": session_id,
                    "user_request": task.user_request,
                    "status": task.status.value,
                    "risk_level": task.risk_level,
                    "plan": _to_jsonb(task.plan),
                    "result": _to_jsonb(task.result),
                    "error_info": _to_jsonb(task.error_info),
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
        self._schedule_sync(task)
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
        
        self._schedule_sync(task)

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
        await bus.publish(VoxEvent(
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
            # Tools that must check what the USER asked (saved logins) read
            # it from here; agents cannot change it.
            with user_request(task.user_request):
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

        import time
        from datetime import datetime, timezone
        from voxos.core.router import get_execution_router, ExecutionClass
        from voxos.core.task_manager.models import TaskPlan, TaskStep
        from voxos.orchestration.graph import should_orchestrate

        # Stage 1: Listener — understand the request
        await self.transition(task, TaskStatus.UNDERSTANDING)
        
        t0 = time.monotonic()
        listener_output = await listener.process(ListenerInput(
            text=task.user_request,
            session_id=task.session_id,
            language=task.language,
        ))
        t1 = time.monotonic()

        task.intent = listener_output.intent
        task.language = listener_output.language
        task.entities = listener_output.entities
        task.turn_finalized_at = datetime.now(timezone.utc)
        task.touch()

        log.info(
            "listener_output",
            task_id=task.id,
            intent=listener_output.intent,
            language=listener_output.language,
            entities=listener_output.entities,
        )

        # Stage 2: Routing
        t2 = time.monotonic()
        router = get_execution_router()
        exec_class = router.route(
            listener_output.intent,
            listener_output.confidence,
            listener_output.entities,
            source=getattr(listener_output, "source", "rules"),
            request_text=task.user_request,
        )
        task.execution_class = exec_class.value
        t3 = time.monotonic()
        task.routing_ms = (t3 - t2) * 1000

        if exec_class == ExecutionClass.REASONING and should_orchestrate(
            task.user_request, get_settings().agent.orchestrator
        ):
            return await self._run_orchestrated(task, executor, overseer, permissions, t0)

        if exec_class in (ExecutionClass.FAST_PATH, ExecutionClass.AUTOMATION):
            log.info("deterministic_path_selected", task_id=task.id, intent=listener_output.intent, exec_class=exec_class.value)
            
            tool_name = (
                router.fast_path_tool(listener_output.intent)
                or router.automation_tool(listener_output.intent)
                or listener_output.intent
            )
            
            task.plan = TaskPlan(
                goal=task.user_request,
                steps=[
                    TaskStep(
                        action=listener_output.intent,
                        tool=tool_name,
                        arguments=listener_output.entities
                    )
                ],
                estimated_risk="LOW"
            )
            task.risk_level = "LOW"
            task.llm_ms = (t1 - t0) * 1000  # Only listener LLM cost
            task.touch()
        else:
            # Stage 2.5: Strategist — create a plan
            await self.transition(task, TaskStatus.PLANNING)
            strat_t0 = time.monotonic()
            strat_output = await strategist.process(StrategistInput(
                intent=listener_output.intent,
                entities=listener_output.entities,
                user_request=task.user_request,
                session_id=task.session_id,
                task_id=task.id,
                language=listener_output.language,
            ))
            strat_t1 = time.monotonic()

            task.plan = strat_output.plan
            task.risk_level = strat_output.plan.estimated_risk
            task.llm_ms = ((t1 - t0) + (strat_t1 - strat_t0)) * 1000
            task.touch()

            log.info(
                "plan_created",
                task_id=task.id,
                steps=len(task.plan.steps),
                risk=task.risk_level,
            )

        # Stage 3: Execute with retry loop
        task.tool_ms = 0.0
        task.verification_ms = 0.0

        for attempt in range(task.max_retries + 1):
            if task.status == TaskStatus.CANCELLED:
                return task

            await self.transition(task, TaskStatus.EXECUTING)

            exec_t0 = time.monotonic()
            executor_output = await executor.process(ExecutorInput(
                task=task,
                session_id=task.session_id,
                permissions=permissions,
            ))
            exec_t1 = time.monotonic()
            task.tool_ms += (exec_t1 - exec_t0) * 1000

            # Fast-fail check for non-retryable errors
            has_fatal_error = False
            for step_res in executor_output.step_results:
                code = step_res.get("error_code")
                if (
                    step_res.get("status") == "FAILED"
                    and code in NON_RETRYABLE_ERRORS
                    and code not in PLANNER_ERRORS
                ):
                    log.warning("fast_failing_on_fatal_error", task_id=task.id, error_code=code)
                    has_fatal_error = True
                    break
            
            if has_fatal_error:
                # We do not even run overseer or replanning
                overseer_output = OverseerOutput(all_passed=False, failures=["Fatal execution error"])
                break

            # Stage 4: Overseer verification
            await self.transition(task, TaskStatus.VERIFYING)
            veri_t0 = time.monotonic()
            overseer_output = await overseer.process(OverseerInput(
                task=task,
                executor_output=executor_output,
                session_id=task.session_id,
            ))
            veri_t1 = time.monotonic()
            task.verification_ms += (veri_t1 - veri_t0) * 1000

            if overseer_output.all_passed:
                task.result = executor_output.results
                task.total_ms = (time.monotonic() - t0) * 1000
                await self.transition(task, TaskStatus.COMPLETED)
                log.info("task_succeeded", task_id=task.id, attempts=attempt + 1)
                return task

            # Verification failed. A deterministic route has no plan of its own
            # to revise, so hand the request and the failure to the Strategist.
            if exec_class in (ExecutionClass.FAST_PATH, ExecutionClass.AUTOMATION):
                task.escalated_from = exec_class.value
                exec_class = ExecutionClass.REASONING
                log.warning("deterministic_route_escalated", task_id=task.id, from_class=task.escalated_from)

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
            "reason": "Max retries exceeded" if attempt >= task.max_retries else "Verification failed",
            "failures": getattr(overseer_output, "failures", []),
        }
        task.total_ms = (time.monotonic() - t0) * 1000
        await self.transition(task, TaskStatus.FAILED)
        log.error("task_failed", task_id=task.id, retries=task.retry_count)
        return task

    async def _run_orchestrated(
        self,
        task: Task,
        executor: Any,
        overseer: Any,
        permissions: PermissionManager | None,
        t0: float,
    ) -> Task:
        """Coordinator → parallel Workers → Synthesizer for one request."""
        import time

        from voxos.core.task_manager.models import StepStatus, TaskPlan, TaskStep

        task.execution_class = "ORCHESTRATED"
        await self.transition(task, TaskStatus.EXECUTING)
        orchestrator = self.orchestrator_factory(executor, overseer)
        outcome = await orchestrator.run(
            task.user_request,
            session_id=task.session_id,
            language=task.language,
            permissions=permissions,
        )

        status_of = {r.goal_id: r.status for r in outcome.results}
        step_status = {"done": StepStatus.COMPLETED, "failed": StepStatus.FAILED}
        task.plan = TaskPlan(
            goal=task.user_request,
            steps=[
                TaskStep(
                    id=goal.id,
                    action=goal.goal,
                    depends_on=goal.depends_on,
                    status=step_status.get(status_of.get(goal.id, ""), StepStatus.SKIPPED),
                )
                for goal in outcome.goals
            ],
        )
        task.result = {
            "reply": outcome.reply,
            "workers": [r.model_dump() for r in outcome.results],
            "questions": outcome.questions,
        }
        task.total_ms = (time.monotonic() - t0) * 1000
        if outcome.succeeded or outcome.questions:  # asking is not failing
            await self.transition(task, TaskStatus.COMPLETED)
        else:
            task.error_info = {
                "reason": "No part of the request succeeded",
                "failures": [r.error for r in outcome.results if r.error],
            }
            await self.transition(task, TaskStatus.FAILED)
        log.info(
            "orchestrated_task_finished",
            task_id=task.id,
            goals=len(outcome.goals),
            done=sum(r.status == "done" for r in outcome.results),
        )
        return task

    def stats(self) -> dict[str, int]:
        """Return task count by status."""
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts


def _default_orchestrator(executor: Any, overseer: Any) -> Any:
    from voxos.orchestration.graph import VoxOrchestrator
    from voxos.orchestration.llm import RouterJsonLLM

    settings = get_settings().agent

    async def recall(session_id: str) -> list[str]:
        # The local profile: instant, and keyed by a user id that survives
        # restarts (the database memory was keyed by session and slow).
        from voxos.core.profile.store import get_profile

        summary = get_profile().summary()
        return [f"About the user:\n{summary}"] if summary else []

    async def observe() -> tuple[str, list[str]]:
        from voxos.tools.browser import tools as browser

        state = await browser.browser_tabs_state()
        return (browser.describe_tabs(state) if state else ""), [t["tab"] for t in state]

    def history(session_id: str) -> list[dict[str, str]]:
        from voxos.core.conversation.service import recent_turns

        return recent_turns(session_id)

    return VoxOrchestrator(
        llm=RouterJsonLLM(),
        executor=executor,
        overseer=overseer,
        recall=recall,
        observe=observe,
        history=history,
        max_goals=settings.max_subgoals,
        worker_steps=settings.worker_max_steps,
    )


# Singleton
_task_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
