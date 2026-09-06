"""
Task State Machine — explicit valid transitions.

Enforces that tasks can only move to valid next states,
preventing invalid state jumps caused by bugs or race conditions.
"""

from __future__ import annotations

from voxos.core.task_manager.models import TaskStatus

# Valid transitions: current_state → set of allowed next states
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.UNDERSTANDING, TaskStatus.CANCELLED},
    TaskStatus.UNDERSTANDING: {TaskStatus.PLANNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PLANNING: {
        TaskStatus.WAITING_FOR_APPROVAL,
        TaskStatus.EXECUTING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_FOR_APPROVAL: {
        TaskStatus.EXECUTING,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.EXECUTING: {
        TaskStatus.VERIFYING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REPLANNING,
    },
    TaskStatus.VERIFYING: {
        TaskStatus.EXECUTING,   # next step
        TaskStatus.COMPLETED,   # all steps passed
        TaskStatus.REPLANNING,  # verification failed → replan
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.REPLANNING: {
        TaskStatus.PLANNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    # Terminal states — no outgoing transitions
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: TaskStatus, attempted: TaskStatus) -> None:
        self.current = current
        self.attempted = attempted
        super().__init__(
            f"Invalid task state transition: {current.value!r} → {attempted.value!r}. "
            f"Allowed: {[s.value for s in VALID_TRANSITIONS.get(current, set())]}"
        )


def validate_transition(current: TaskStatus, new: TaskStatus) -> None:
    """
    Raise InvalidTransitionError if the transition is not allowed.

    Call this before calling task.set_status().
    """
    allowed = VALID_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise InvalidTransitionError(current, new)


def is_terminal(status: TaskStatus) -> bool:
    """Return True if the status is a terminal (final) state."""
    return status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


def can_cancel(status: TaskStatus) -> bool:
    """Return True if a task in this status can be cancelled."""
    return TaskStatus.CANCELLED in VALID_TRANSITIONS.get(status, set())
