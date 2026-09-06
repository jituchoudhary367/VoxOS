"""
Audit Logging.

Provides an immutable trail of HIGH-risk actions taken by the agent,
including who authorized them and what the exact parameters were.
"""

from __future__ import annotations

from typing import Any
import json

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class AuditLogger:
    """Records significant agent actions for compliance and review."""

    @staticmethod
    async def log_action(
        action: str,
        task_id: str,
        user_id: str,
        parameters: dict[str, Any],
        status: str,
        approved_by: str | None = None,
    ) -> None:
        """
        Log an auditable action.
        
        In Stage 1, we just write it to our structured log stream.
        In Stage 2, this will also write to the AuditLog DB table.
        """
        # Redact parameters before logging
        from voxos.security.secrets import redact_dict
        safe_params = redact_dict(parameters)
        
        log.info(
            "audit_event",
            audit_action=action,
            task_id=task_id,
            user_id=user_id,
            parameters=json.dumps(safe_params),
            audit_status=status,
            approved_by=approved_by or "system",
        )
