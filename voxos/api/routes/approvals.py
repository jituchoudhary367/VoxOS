"""
Approvals route — human-in-the-loop approval gateway.

When VoxOS plans a HIGH-risk action, it pauses execution and posts
an approval request here. The user must explicitly approve or reject it
before the action proceeds.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from voxos.core.risk_engine.approval import get_approval_gateway
from voxos.core.risk_engine.engine import get_risk_engine
from voxos.observability.logger import get_logger

router = APIRouter()
log = get_logger(__name__)


class ApprovalResponse(BaseModel):
    approved: bool


@router.get("/approvals", response_model=list[dict])
async def list_pending_approvals():
    """List all pending approval requests."""
    engine = get_risk_engine()
    return [a.to_dict() for a in engine.get_pending_approvals()]


@router.get("/approvals/{approval_id}", response_model=dict)
async def get_approval(approval_id: str):
    """Get a specific approval request."""
    engine = get_risk_engine()
    approval = engine.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return approval.to_dict()


@router.post("/approvals/{approval_id}/respond", response_model=dict)
async def respond_to_approval(approval_id: str, body: ApprovalResponse):
    """
    Approve or reject a pending action.
    
    Set approved=true to allow the action, approved=false to reject it.
    """
    engine = get_risk_engine()
    approval = await engine.process_approval_response(
        approval_id=approval_id,
        approved=body.approved,
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    # Wake ToolRunner waiters immediately. ExecutorAgent also observes the
    # RiskEngine status, so resolving the gateway keeps both execution paths
    # consistent.
    get_approval_gateway().resolve(approval_id, body.approved)

    log.info(
        "approval_responded",
        approval_id=approval_id,
        approved=body.approved,
        tool=approval.tool_name,
    )
    return {
        "approval_id": approval_id,
        "status": approval.status,
        "message": (
            f"Action '{approval.tool_name}' has been {'approved' if body.approved else 'rejected'}."
        ),
    }
