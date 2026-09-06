"""
Event Handlers — concrete in-process handlers for the EventBus.

These run in the same process as the event publisher and handle
cross-cutting concerns like audit logging, metrics, and WebSocket push.
"""

from __future__ import annotations

from voxos.core.events.bus import JarvisEvent
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


async def handle_task_completed(event: JarvisEvent) -> None:
    """Log and count completed tasks."""
    log.info(
        "event_task_completed",
        task_id=event.task_id,
        session_id=event.session_id,
        payload=event.payload,
    )
    metrics.increment("tasks.completed_events")


async def handle_task_failed(event: JarvisEvent) -> None:
    """Log failed tasks with context."""
    log.error(
        "event_task_failed",
        task_id=event.task_id,
        session_id=event.session_id,
        payload=event.payload,
    )
    metrics.increment("tasks.failed_events")


async def handle_approval_required(event: JarvisEvent) -> None:
    """Log approval requests — UI should surface these to user."""
    log.warning(
        "event_approval_required",
        task_id=event.task_id,
        tool=event.tool,
        risk_level=event.payload.get("risk_level"),
    )
    metrics.increment("approvals.requested")


async def handle_approval_granted(event: JarvisEvent) -> None:
    log.info("event_approval_granted", task_id=event.task_id, payload=event.payload)
    metrics.increment("approvals.granted")


async def handle_approval_rejected(event: JarvisEvent) -> None:
    log.info("event_approval_rejected", task_id=event.task_id, payload=event.payload)
    metrics.increment("approvals.rejected")


async def handle_tool_failed(event: JarvisEvent) -> None:
    log.error(
        "event_tool_failed",
        task_id=event.task_id,
        tool=event.tool,
        payload=event.payload,
    )
    metrics.increment("tools.failed_events", tool=event.tool or "unknown")


async def handle_verification_failed(event: JarvisEvent) -> None:
    log.warning(
        "event_verification_failed",
        task_id=event.task_id,
        payload=event.payload,
    )
    metrics.increment("overseer.verification_failures")


def register_default_handlers() -> None:
    """Register all default in-process event handlers on the global EventBus."""
    from voxos.core.events.bus import get_event_bus
    from voxos.core.events.types import EventType

    bus = get_event_bus()
    bus.subscribe(EventType.TASK_COMPLETED, handle_task_completed)
    bus.subscribe(EventType.TASK_FAILED, handle_task_failed)
    bus.subscribe(EventType.APPROVAL_REQUIRED, handle_approval_required)
    bus.subscribe(EventType.APPROVAL_GRANTED, handle_approval_granted)
    bus.subscribe(EventType.APPROVAL_REJECTED, handle_approval_rejected)
    bus.subscribe(EventType.TOOL_FAILED, handle_tool_failed)
    bus.subscribe(EventType.VERIFICATION_FAILED, handle_verification_failed)
    log.info("event_handlers_registered")
