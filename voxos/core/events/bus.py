"""
VoxOS Event Bus — Redis-backed pub/sub for internal events.

Events flow through Redis channels so any component can subscribe
to state changes without tight coupling.

Design:
- Publish: fire-and-forget with structured JSON payload
- Subscribe: async generator pattern for consumers
- In-process listeners: registered callbacks for same-process consumers
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Coroutine

import redis.asyncio as aioredis
import certifi

from voxos.config import get_settings
from voxos.core.events.types import EventType
from voxos.observability.logger import get_logger

log = get_logger(__name__)

EventHandler = Callable[["VoxEvent"], Coroutine[Any, Any, None]]


@dataclass
class VoxEvent:
    """A typed internal event."""

    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str | None = None
    task_id: str | None = None
    agent: str | None = None
    tool: str | None = None
    correlation_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "VoxEvent":
        d = json.loads(data)
        d["event_type"] = EventType(d["event_type"])
        return cls(**d)


class EventBus:
    """
    Redis-backed event bus with in-process listener support.
    
    Usage:
        bus = get_event_bus()
        
        # Publish
        await bus.publish(VoxEvent(
            event_type=EventType.TASK_STARTED,
            task_id=str(task.id),
            agent="strategist",
        ))
        
        # Subscribe (in-process)
        bus.subscribe(EventType.APPROVAL_REQUIRED, my_handler)
    """

    CHANNEL_PREFIX = "voxos:events:"
    GLOBAL_CHANNEL = "voxos:events:all"

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []
        self._connected = False
        # Redis copies of events, sent in order by one background sender so
        # a remote Redis round trip never delays the publisher.
        self._outbox: list[tuple[str, str]] = []
        self._sender: asyncio.Task | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        settings = get_settings().redis
        redis_kwargs = {
            "decode_responses": True,
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
        }
        if settings.url_override:
            if settings.url.startswith("rediss://"):
                redis_kwargs["ssl_ca_certs"] = settings.ca_certs or certifi.where()
            self._redis = aioredis.from_url(settings.url, **redis_kwargs)
        else:
            self._redis = aioredis.Redis(
                host=settings.host,
                port=settings.port,
                db=settings.db,
                password=settings.password,
                **redis_kwargs,
            )
        try:
            await asyncio.wait_for(self._redis.ping(), timeout=2.5)
        except Exception:
            try:
                await asyncio.wait_for(self._redis.close(), timeout=1)
            except Exception:
                pass
            self._redis = None
            raise
        self._connected = True
        log.info("event_bus_connected", host=settings.host, port=settings.port)

    async def disconnect(self) -> None:
        """Close Redis connection."""
        try:
            await asyncio.wait_for(self.flush(), timeout=5)
        except Exception:
            log.warning("event_outbox_not_flushed", pending=len(self._outbox) // 2)
        if self._redis:
            await self._redis.close()
            self._connected = False
            log.info("event_bus_disconnected")

    OUTBOX_LIMIT = 2000

    async def _send_outbox(self) -> None:
        """Send queued events in order, batching everything waiting into one round trip."""
        while self._outbox and self._redis is not None:
            batch, self._outbox = self._outbox, []
            try:
                async with self._redis.pipeline(transaction=False) as pipe:
                    for channel, message in batch:
                        pipe.publish(channel, message)
                    await pipe.execute()
            except Exception as e:
                log.warning("event_redis_publish_failed", events=len(batch) // 2, error=str(e))

    async def flush(self) -> None:
        """Wait until every queued event has been sent to Redis."""
        while self._sender is not None and not self._sender.done():
            await asyncio.gather(self._sender, return_exceptions=True)

    async def publish(self, event: VoxEvent) -> None:
        """
        Publish an event to Redis and trigger in-process handlers.
        
        Both the global channel and the event-specific channel receive the event.
        """
        # In-process handlers first (same-process consumers)
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                log.error(
                    "event_handler_error",
                    event_type=event.event_type,
                    handler=handler.__name__,
                    error=str(e),
                )

        for handler in self._global_handlers:
            try:
                await handler(event)
            except Exception as e:
                log.error(
                    "global_event_handler_error",
                    event_type=event.event_type,
                    handler=handler.__name__,
                    error=str(e),
                )

        # Redis pub/sub for cross-process consumers, off the request path:
        # each remote round trip took 0.3-1 s and a task publishes ~11 events.
        if self._connected and self._redis:
            event_json = event.to_json()
            if len(self._outbox) >= self.OUTBOX_LIMIT:
                log.warning("event_outbox_full_dropping", event_type=event.event_type)
            else:
                self._outbox.append((f"{self.CHANNEL_PREFIX}{event.event_type}", event_json))
                self._outbox.append((self.GLOBAL_CHANNEL, event_json))
                if self._sender is None or self._sender.done():
                    self._sender = asyncio.create_task(self._send_outbox())

        log.debug(
            "event_published",
            event_type=event.event_type,
            event_id=event.event_id,
            session_id=event.session_id,
            task_id=event.task_id,
        )

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register an in-process event handler."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        log.debug("event_handler_registered", event_type=event_type, handler=handler.__name__)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove an in-process event handler."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register an in-process handler for every event type."""
        if handler not in self._global_handlers:
            self._global_handlers.append(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Remove a global event handler."""
        self._global_handlers = [h for h in self._global_handlers if h is not handler]

    async def subscribe_redis(
        self,
        event_types: list[EventType] | None = None,
    ) -> AsyncIterator[VoxEvent]:
        """
        Subscribe to Redis channels and yield events as they arrive.
        
        For cross-process consumers (e.g., dashboard WebSocket stream).
        """
        if not self._connected or not self._redis:
            raise RuntimeError("EventBus not connected. Call connect() first.")

        pubsub = self._redis.pubsub()

        if event_types:
            channels = [f"{self.CHANNEL_PREFIX}{et}" for et in event_types]
        else:
            channels = [self.GLOBAL_CHANNEL]

        await pubsub.subscribe(*channels)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = VoxEvent.from_json(message["data"])
                        yield event
                    except Exception as e:
                        log.warning("event_deserialize_error", error=str(e))
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.close()


# Singleton
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
