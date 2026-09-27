"""Task state is persisted in the background, never on the reply path.

Each write to the remote Postgres took 2-7 s and a task writes on every
status change, so waiting on them made a 1 s request take 17-19 s.
"""

from __future__ import annotations

import asyncio

from voxos.core.task_manager.manager import TaskManager
from voxos.core.task_manager.models import TaskStatus


class SlowDB:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.writes: list[str] = []

    async def sync(self, task) -> None:
        await self.release.wait()
        self.writes.append(task.status.value)


async def test_status_changes_do_not_wait_for_the_database():
    manager = TaskManager()
    db = SlowDB()
    manager._sync_task_to_db = db.sync

    task = await asyncio.wait_for(manager.create_task("s", "what time is it"), timeout=1)
    await asyncio.wait_for(manager.transition(task, TaskStatus.UNDERSTANDING), timeout=1)
    await asyncio.wait_for(manager.transition(task, TaskStatus.EXECUTING), timeout=1)
    await asyncio.wait_for(manager.transition(task, TaskStatus.COMPLETED), timeout=1)

    assert db.writes == []
    db.release.set()
    await manager.flush()
    assert db.writes[-1] == "COMPLETED"


async def test_writes_for_one_task_are_serial_and_coalesced():
    manager = TaskManager()
    db = SlowDB()
    manager._sync_task_to_db = db.sync

    task = await manager.create_task("s", "x")  # first write starts, blocks
    for status in (TaskStatus.UNDERSTANDING, TaskStatus.PLANNING, TaskStatus.EXECUTING, TaskStatus.COMPLETED):
        await manager.transition(task, status)
    db.release.set()
    await manager.flush()

    # At most the in-flight write plus one of the latest state, not five.
    assert 1 <= len(db.writes) <= 2
    assert db.writes[-1] == "COMPLETED"


async def test_a_failing_write_is_logged_not_raised():
    manager = TaskManager()

    async def broken(task):
        raise RuntimeError("database down")

    manager._sync_task_to_db = broken
    task = await manager.create_task("s", "x")
    await manager.transition(task, TaskStatus.COMPLETED)
    await manager.flush()

    assert task.status == TaskStatus.COMPLETED


async def test_session_created_concurrently_by_another_task_is_not_an_error(monkeypatch):
    """Parallel tasks of one session raced to insert the session row."""
    import uuid

    from sqlalchemy.exc import IntegrityError

    from voxos.core.task_manager import manager as manager_module
    from voxos.core.task_manager.models import Task

    calls: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class SessionRepo:
        def __init__(self, _s):
            pass

        async def get(self, _v):
            return None

        async def create(self, _p):
            raise IntegrityError("insert", {}, Exception("duplicate key sessions_pkey"))

    class TaskRepo:
        def __init__(self, _s):
            pass

        async def get(self, _v):
            return None

        async def create(self, _p):
            calls.append("task_create")

    monkeypatch.setattr(manager_module, "get_session_factory", lambda: (lambda: FakeSession()))
    monkeypatch.setattr(manager_module, "SessionRepository", SessionRepo)
    monkeypatch.setattr(manager_module, "TaskRepository", TaskRepo)
    monkeypatch.setattr(manager_module.get_settings(), "env", "production", raising=False)

    task = Task(session_id=str(uuid.uuid4()), user_request="x")
    await manager_module.TaskManager()._sync_task_to_db(task)

    assert calls == ["rollback", "task_create", "commit"]
