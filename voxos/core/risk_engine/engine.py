"""
Risk Engine — combines tool metadata risk with command classification.

Flow:
    Tool Request → RiskEngine.evaluate() → RiskDecision
        LOW    → Execute immediately
        MEDIUM → Log + Execute
        HIGH   → Route to ApprovalGateway → wait → Execute or reject
        CRITICAL → Block (return rejection)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from voxos.core.events.bus import VoxEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.risk_engine.classifier import ClassificationResult, CommandRiskClassifier
from voxos.tools.base import BaseTool, RiskLevel
from voxos.observability.logger import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    """Decision returned by the Risk Engine."""

    risk_level: RiskLevel
    requires_approval: bool
    is_blocked: bool = False
    block_reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    approval_request_id: str | None = None

    @property
    def can_execute(self) -> bool:
        return not self.is_blocked and not self.requires_approval

    @property
    def needs_approval(self) -> bool:
        return self.requires_approval and not self.is_blocked


@dataclass
class ApprovalRequest:
    """Pending approval waiting for user response."""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel
    requested_action: str
    reason: str
    consequences: list[str]
    task_id: str | None
    session_id: str | None
    created_at: datetime
    expires_at: datetime
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED | EXPIRED

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "requested_action": self.requested_action,
            "reason": self.reason,
            "consequences": self.consequences,
            "status": self.status,
            "task_id": self.task_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class RiskEngine:
    """
    Evaluates tool calls and commands for risk and determines
    whether execution can proceed or requires human approval.
    """

    def __init__(self) -> None:
        self._classifier = CommandRiskClassifier()
        self._pending_approvals: dict[str, ApprovalRequest] = {}

    def evaluate_tool(
        self,
        tool: BaseTool,
        arguments: dict[str, Any],
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> RiskDecision:
        """
        Evaluate a tool call and return a risk decision.
        
        Uses the tool's declared risk level as base, then may escalate
        based on the actual arguments (e.g., dangerous command in terminal.execute).
        """
        meta = tool.metadata

        # Start with the tool's declared risk level
        base_risk = meta.risk_level

        # For terminal/filesystem operations, do additional classification
        cmd_classification: ClassificationResult | None = None
        if meta.name in {
            "terminal.execute",
            "os.shell_command",
            "filesystem.delete",
            "filesystem.archive",
        }:
            cmd_classification = self._classifier.classify_tool_operation(
                meta.name, arguments
            )
            # Take the higher of the two risk levels
            base_risk = self._max_risk(base_risk, cmd_classification.risk_level)

        # Apply decision rules
        if base_risk == RiskLevel.CRITICAL:
            block_reason = (
                cmd_classification.block_reason
                if cmd_classification and cmd_classification.is_blocked
                else "Critical risk operations are blocked by policy"
            )
            log.warning(
                "tool_blocked_critical",
                tool=meta.name,
                task_id=task_id,
            )
            return RiskDecision(
                risk_level=RiskLevel.CRITICAL,
                requires_approval=False,
                is_blocked=True,
                block_reason=block_reason,
                reasons=cmd_classification.reasons if cmd_classification else ["CRITICAL risk"],
            )

        if base_risk == RiskLevel.HIGH or meta.requires_confirmation:
            return RiskDecision(
                risk_level=base_risk,
                requires_approval=True,
                reasons=cmd_classification.reasons if cmd_classification else ["HIGH risk operation"],
            )

        return RiskDecision(
            risk_level=base_risk,
            requires_approval=False,
            reasons=["Approved automatically"],
        )

    async def request_approval(
        self,
        tool: BaseTool,
        arguments: dict[str, Any],
        decision: RiskDecision,
        task_id: str | None = None,
        session_id: str | None = None,
        timeout_seconds: int = 120,
    ) -> ApprovalRequest:
        """
        Create an approval request and emit an event.
        
        The Executor must wait for this request to be approved/rejected
        before proceeding.
        """
        meta = tool.metadata
        approval_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc)
        approval = ApprovalRequest(
            id=approval_id,
            tool_name=meta.name,
            arguments=self._sanitize_arguments(arguments),
            risk_level=decision.risk_level,
            requested_action=f"Execute {meta.name}",
            reason=meta.description,
            consequences=decision.reasons,
            task_id=task_id,
            session_id=session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=timeout_seconds),
        )

        self._pending_approvals[approval_id] = approval

        # Emit approval required event
        bus = get_event_bus()
        await bus.publish(VoxEvent(
            event_type=EventType.APPROVAL_REQUIRED,
            payload=approval.to_dict(),
            task_id=task_id,
            session_id=session_id,
            agent="risk_engine",
            tool=meta.name,
        ))

        log.info(
            "approval_requested",
            approval_id=approval_id,
            tool=meta.name,
            risk=decision.risk_level,
            task_id=task_id,
            expires_at=approval.expires_at.isoformat(),
        )

        return approval

    async def process_approval_response(
        self,
        approval_id: str,
        approved: bool,
    ) -> ApprovalRequest | None:
        """Process a user's approval or rejection."""
        approval = self._pending_approvals.get(approval_id)
        if not approval:
            log.warning("approval_not_found", approval_id=approval_id)
            return None

        if approval.is_expired():
            approval.status = "EXPIRED"
            bus = get_event_bus()
            await bus.publish(VoxEvent(
                event_type=EventType.APPROVAL_EXPIRED,
                payload={"approval_id": approval_id},
                task_id=approval.task_id,
            ))
            return approval

        approval.status = "APPROVED" if approved else "REJECTED"

        bus = get_event_bus()
        event_type = EventType.APPROVAL_GRANTED if approved else EventType.APPROVAL_REJECTED
        await bus.publish(VoxEvent(
            event_type=event_type,
            payload={"approval_id": approval_id, "status": approval.status},
            task_id=approval.task_id,
            session_id=approval.session_id,
        ))

        log.info(
            "approval_processed",
            approval_id=approval_id,
            status=approval.status,
            tool=approval.tool_name,
        )
        return approval

    def get_pending_approvals(self) -> list[ApprovalRequest]:
        """Return all non-expired pending approvals."""
        return [
            a for a in self._pending_approvals.values()
            if a.status == "PENDING" and not a.is_expired()
        ]

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        return self._pending_approvals.get(approval_id)

    @staticmethod
    def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        return b if order.index(b) > order.index(a) else a

    @staticmethod
    def _sanitize_arguments(args: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive data before storing in approval requests."""
        SENSITIVE = {"password", "api_key", "secret", "token", "key", "credential"}
        return {
            k: "***" if any(s in k.lower() for s in SENSITIVE) else v
            for k, v in args.items()
        }


# Singleton
_risk_engine: RiskEngine | None = None


def get_risk_engine() -> RiskEngine:
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine()
    return _risk_engine
