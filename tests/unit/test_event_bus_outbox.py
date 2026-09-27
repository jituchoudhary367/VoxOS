"""Redis publishing happens off the request path.

Upstash is remote: each event cost two 0.3-1 s round trips and a task
publishes ~11 events, adding 6 s to every request.
"""

from __future__ import annotations

import asyncio
import json

from voxos.core.events.bus import EventBus, VoxEvent
from voxos.core.events.types import EventType


class SlowPipeline:
    def __init__(self, redis: "SlowRedis") -> None:
        self.redis = redis
        self.pending: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.pending.append((channel, message))

    async def execute(self) -> None:
        await self.redis.release.wait()
        self.redis.round_trips += 1
        self.redis.published.extend(self.pending)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class SlowRedis:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.published: list[tuple[str, str]] = []
        self.round_trips = 0

    def pipeline(self, transaction: bool = True) -> SlowPipeline:
        return SlowPipeline(self)


def _bus(redis: SlowRedis) -> EventBus:
    bus = EventBus()
    bus._redis = redis
    bus._connected = True
    return bus


def _event(n: int) -> VoxEvent:
    return VoxEvent(event_type=EventType.TASK_STARTED, payload={"n": n}, task_id="t")


async def test_publish_returns_before_redis_answers_but_handlers_run_now():
    redis = SlowRedis()
    bus = _bus(redis)
    seen = []

    async def handler(event):
        seen.append(event.payload["n"])

    bus.subscribe(EventType.TASK_STARTED, handler)
    await asyncio.wait_for(bus.publish(_event(1)), timeout=0.5)

    assert seen == [1]
    assert redis.published == []
    redis.release.set()
    await bus.flush()
    assert len(redis.published) == 2  # event channel + global channel


async def test_queued_events_are_sent_in_order_in_few_round_trips():
    redis = SlowRedis()
    bus = _bus(redis)

    for n in range(11):
        await bus.publish(_event(n))
    redis.release.set()
    await bus.flush()

    global_order = [m for c, m in redis.published if c == EventBus.GLOBAL_CHANNEL]
    assert [json.loads(m)["payload"]["n"] for m in global_order] == list(range(11))
    assert redis.round_trips <= 2


async def test_redis_failure_is_logged_and_the_sender_keeps_going():
    class BrokenRedis(SlowRedis):
        def pipeline(self, transaction: bool = True):
            raise ConnectionError("redis down")

    bus = _bus(BrokenRedis())
    await bus.publish(_event(1))
    await asyncio.wait_for(bus.flush(), timeout=1)
