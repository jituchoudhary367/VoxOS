"""
Task Manager Pydantic models.

These are the in-memory/API models (not SQLAlchemy ORM).
They represent tasks flowing through the agent system.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Full task lifecycle states."""

    CREATED = "CREATED"
    PENDING = "CREATED"  # Backwards-compatible alias
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskStep(BaseModel):
    """A single step in a task execution plan."""

    id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}")
    action: str
    description: str = ""
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_ready(self) -> bool:
        """True if this step has no unresolved dependencies."""
        return not self.depends_on

    def duration_ms(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None


class TaskPlan(BaseModel):
    """A structured execution plan produced by the Strategist."""

    goal: str
    steps: list[TaskStep] = Field(default_factory=list)
    estimated_risk: str = "LOW"
    reasoning: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_step(self, step_id: str) -> TaskStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def ready_steps(self, completed_ids: set[str]) -> list[TaskStep]:
        """Return steps whose dependencies are all completed."""
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING
            and all(dep in completed_ids for dep in s.depends_on)
        ]

    def all_completed(self) -> bool:
        return all(s.status in {StepStatus.COMPLETED, StepStatus.SKIPPED} for s in self.steps)

    def any_failed(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)


class Task(BaseModel):
    """
    In-memory task representation flowing through the agent pipeline.
    
    This is the live task object — persisted to DB via repositories.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    user_request: str
    status: TaskStatus = TaskStatus.CREATED
    risk_level: str = "LOW"
    plan: TaskPlan | None = None
    current_step_id: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    approval_status: str | None = None
    result: dict[str, Any] | None = None
    error_info: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Extracted intent from Listener
    intent: str | None = None
    language: str = "en"
    entities: dict[str, Any] = Field(default_factory=dict)

    # Phase 9: Latency & Execution Instrumentation
    execution_class: str | None = None
    # Set when a FAST_PATH/AUTOMATION attempt failed and the Strategist took over.
    escalated_from: str | None = None
    turn_finalized_at: datetime | None = None
    routing_ms: float | None = None
    llm_ms: float | None = None
    tool_ms: float | None = None
    verification_ms: float | None = None
    tts_ms: float | None = None
    total_ms: float | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def increment_retry(self) -> None:
        self.retry_count += 1
        self.touch()

    def set_status(self, status: TaskStatus) -> None:
        self.status = status
        self.touch()

    def to_summary(self) -> dict[str, Any]:
        """Compact summary for API responses."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "status": self.status,
            "user_request": self.user_request[:200],
            "risk_level": self.risk_level,
            "intent": self.intent,
            "language": self.language,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "steps_total": len(self.plan.steps) if self.plan else 0,
            "steps_done": (
                sum(1 for s in self.plan.steps if s.status == StepStatus.COMPLETED)
                if self.plan else 0
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# API schemas (request/response contracts)
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Request body for POST /api/v1/chat."""

    message: str = Field(min_length=1, max_length=10_000)
    session_id: str | None = None
    input_type: str = "text"  # text | voice
    language: str = "auto"

    model_config = {"json_schema_extra": {"example": {"message": "What time is it?", "session_id": None}}}


class ChatResponse(BaseModel):
    """Response from POST /api/v1/chat."""

    response: str
    session_id: str
    task_id: str | None = None
    task_status: str | None = None
    intent: str | None = None
    language: str = "en"
    requires_approval: bool = False
    approval_id: str | None = None
