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
