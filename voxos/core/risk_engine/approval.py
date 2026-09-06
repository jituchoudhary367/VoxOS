"""
Approval Gateway — waits for user approval on HIGH-risk tool calls.

Separate from the RiskEngine so concerns are cleanly separated:
- RiskEngine: evaluate + create approval request
- ApprovalGateway: wait for user response (polling + asyncio.Event)
"""

from __future__ import annotations

import asyncio
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class ApprovalGateway:
    """
    Manages waiting for user approvals.

    When the RiskEngine requires approval, it creates an ApprovalRequest
    and emits APPROVAL_REQUIRED. The Gateway then waits (with timeout)
    for the user to approve or reject via the API.
    """

    def __init__(self) -> None:
        # approval_id → asyncio.Event signalled when a response arrives
        self._events: dict[str, asyncio.Event] = {}
        # approval_id → True (approved) | False (rejected)
        self._decisions: dict[str, bool] = {}

    def register(self, approval_id: str) -> None:
        """Register a new approval request to wait on."""
        self._events[approval_id] = asyncio.Event()

    async def wait_for_decision(
        self,
        approval_id: str,
        timeout_seconds: float = 120.0,
    ) -> bool | None:
        """
        Block until the user approves/rejects or timeout expires.

        Returns:
            True — approved
            False — rejected
            None — timed out
        """
        event = self._events.get(approval_id)
        if not event:
            log.warning("approval_gateway_unknown_id", approval_id=approval_id)
            return None

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
            return self._decisions.get(approval_id)
        except asyncio.TimeoutError:
            log.warning("approval_gateway_timeout", approval_id=approval_id)
            return None
        finally:
            self._cleanup(approval_id)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """
        Signal a waiting coroutine with the user's decision.

        Returns False if the approval_id is unknown (already expired).
        """
        event = self._events.get(approval_id)
        if not event:
            return False
        self._decisions[approval_id] = approved
        event.set()
        log.info(
            "approval_gateway_resolved",
            approval_id=approval_id,
            approved=approved,
        )
        return True

    def _cleanup(self, approval_id: str) -> None:
        self._events.pop(approval_id, None)
        self._decisions.pop(approval_id, None)

    def pending_count(self) -> int:
        return len(self._events)


# Singleton
_gateway: ApprovalGateway | None = None


def get_approval_gateway() -> ApprovalGateway:
    global _gateway
    if _gateway is None:
        _gateway = ApprovalGateway()
    return _gateway
