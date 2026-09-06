"""
Audit API Endpoints.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.api.deps import get_db_session
from voxos.database.repositories.audit_repo import AuditRepository

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/")
async def get_audit_logs(
    session_id: str | None = None,
    task_id: str | None = None,
    event_type: str | None = None,
    risk_level: str | None = None,
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Retrieve audit logs with filtering."""
    repo = AuditRepository(db)
    logs = await repo.list(
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        risk_level=risk_level,
        limit=limit,
    )
    return {
        "logs": [
            {
                "id": log.id,
                "event_type": log.event_type,
                "agent": log.agent,
                "tool": log.tool,
                "action": log.action,
                "risk_level": log.risk_level,
                "status": log.status,
                "duration_ms": log.duration_ms,
                "session_id": log.session_id,
                "task_id": log.task_id,
                "created_at": log.created_at,
            }
            for log in logs
        ]
    }
