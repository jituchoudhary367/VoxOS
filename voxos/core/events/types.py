"""
Event types for the VoxOS internal event bus.

Every meaningful state transition emits a typed event.
This makes the system observable and debuggable.
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    """All internal VoxOS event types."""

    # Input events
    USER_INPUT_RECEIVED = "user_input_received"
    VOICE_TRANSCRIBED = "voice_transcribed"

    # Listener events
    INTENT_DETECTED = "intent_detected"
    INTENT_AMBIGUOUS = "intent_ambiguous"
    LANGUAGE_DETECTED = "language_detected"

    # Planning events
    PLAN_CREATED = "plan_created"
    PLAN_FAILED = "plan_failed"

    # Task lifecycle
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_STEP_STARTED = "task_step_started"
    TASK_STEP_COMPLETED = "task_step_completed"
    TASK_STEP_FAILED = "task_step_failed"
    TASK_REPLANNED = "task_replanned"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    # Tool events
    TOOL_REQUESTED = "tool_requested"
    TOOL_VALIDATED = "tool_validated"
    TOOL_VALIDATION_FAILED = "tool_validation_failed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"

    # Approval events
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"

    # Verification events
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"

    # Memory events
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"

    # System events
    AGENT_STARTED = "agent_started"
    AGENT_STOPPED = "agent_stopped"
    SYSTEM_ERROR = "system_error"
