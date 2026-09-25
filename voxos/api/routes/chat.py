"""
Chat route — main user interaction endpoint.

POST /api/v1/chat  → process a text/voice message through the agent pipeline
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from voxos.agents.executor.agent import ExecutorAgent
from voxos.agents.listener.agent import ListenerAgent
from voxos.agents.overseer.agent import OverseerAgent
from voxos.agents.strategist.agent import StrategistAgent
from voxos.core.task_manager.manager import get_task_manager
from voxos.core.task_manager.models import ChatRequest, ChatResponse
from voxos.api.deps import get_current_user_permissions
from voxos.core.permissions.manager import PermissionManager
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()

router = APIRouter()

# Agent singletons
_listener: ListenerAgent | None = None
_strategist: StrategistAgent | None = None
_executor: ExecutorAgent | None = None
_overseer: OverseerAgent | None = None


def _get_agents():
    global _listener, _strategist, _executor, _overseer
    if _listener is None:
        _listener = ListenerAgent()
        _strategist = StrategistAgent()
        _executor = ExecutorAgent()
        _overseer = OverseerAgent()
    return _listener, _strategist, _executor, _overseer


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(
    request: ChatRequest,
    permissions: PermissionManager = Depends(get_current_user_permissions),
) -> ChatResponse:
    """
    Main chat endpoint — the entry point for all user interactions.
    
    Routes the message through the full agent pipeline:
    Listener → Strategist → Executor → Overseer
    
    For simple queries (get_current_time, etc.) responds in ~1-2 seconds.
    For complex tasks, may include an approval_id if human approval is required.
    """
    session_id = request.session_id or str(uuid.uuid4())
    listener, strategist, executor, overseer = _get_agents()
    task_manager = get_task_manager()

    log.info("chat_request", session_id=session_id, message_length=len(request.message))
    metrics.increment("api.chat_requests")

    # Create task
    task = await task_manager.create_task(
        session_id=session_id,
        user_request=request.message,
        language=request.language,
    )

    try:
        # Run the full pipeline
        task = await task_manager.run_pipeline(
            task=task,
            listener=listener,
            strategist=strategist,
            executor=executor,
            overseer=overseer,
            permissions=permissions,
        )

        # Build response text from task result
        response_text = _build_response(task, request.message)

        # Check for pending approvals
        from voxos.core.risk_engine.engine import get_risk_engine
        risk_engine = get_risk_engine()
        pending = risk_engine.get_pending_approvals()
        task_approval = next((a for a in pending if a.task_id == task.id), None)

        return ChatResponse(
            response=response_text,
            session_id=session_id,
            task_id=task.id,
            task_status=task.status.value,
            intent=task.intent,
            language=task.language,
            requires_approval=bool(task_approval),
            approval_id=task_approval.id if task_approval else None,
        )

    except Exception as e:
        log.error("chat_pipeline_error", session_id=session_id, error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline error: {str(e)}",
        )


def _build_response(task: Any, original_request: str) -> str:
    """Build a human-readable response from task results.

    Result rendering lives in ConversationService so the API, the voice bridge
    and the desktop UI all speak with one voice. Only the status-specific
    branches below are particular to this endpoint.
    """
    from voxos.core.conversation.service import ConversationService
    from voxos.core.task_manager.models import TaskStatus

    if task.status == TaskStatus.WAITING_FOR_APPROVAL:
        return "This action requires your approval. Please check the approval request."

    if task.status == TaskStatus.FAILED:
        error = task.error_info or {}
        return f"I wasn't able to complete that task. {error.get('reason', 'An error occurred.')}"

    if task.result:
        return ConversationService._reply_from_task(task).text

    return f"Task completed with status: {task.status.value}"
