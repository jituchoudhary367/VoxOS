"""Voice and API replies must come from the same renderer as the desktop UI.

The voice bridge used to import a private formatter from the chat route, so
every fix to the conversation boundary skipped the voice path entirely.
"""

from __future__ import annotations

import pytest


def _completed_task(*step_data: dict, intent: str | None = None, status_value: str = "COMPLETED"):
    from voxos.core.task_manager.models import TaskStatus

    return type(
        "CompletedTask",
        (),
        {
            "status": TaskStatus.COMPLETED if status_value == "COMPLETED" else status_value,
            "result": {
                f"step_{index + 1}": {"status": "SUCCESS", "data": data}
                for index, data in enumerate(step_data)
            },
            "error_info": None,
            "intent": intent,
        },
    )()


def test_api_reply_names_the_search_engine_and_query() -> None:
    from voxos.api.routes.chat import _build_response

    task = _completed_task({"engine": "bing", "query": "sap tutorial"}, intent="search_web_browser")

    assert _build_response(task, "search for sap tutorial") == "Searched Bing for sap tutorial."


def test_api_reply_speaks_the_time_for_any_time_intent_alias() -> None:
    from voxos.api.routes.chat import _build_response

    task = _completed_task(
        {"formatted": "2026-09-18 12:16:00", "hour": 12, "minute": 16, "weekday": "Friday"},
        intent="get_time",
    )

    reply = _build_response(task, "what is the time")

    assert "12:16" in reply
    assert "Friday" in reply


def test_api_reply_reports_every_step_of_a_parallel_task() -> None:
    from voxos.api.routes.chat import _build_response

    task = _completed_task(
        {"formatted": "2026-09-18 12:16:00", "hour": 12, "minute": 16, "weekday": "Friday"},
        {"os": {"system": "Windows", "release": "11"}, "host": {"hostname": "DESKTOP-ABC"}},
        {"cpu": {"percent": 21.7}, "memory": {"percent": 96.5, "used_gb": 15.1, "total_gb": 15.7}},
        intent="parallel_info",
    )

    reply = _build_response(task, "time, system info and ram")

    assert "12:16" in reply
    assert "Windows" in reply
    assert "96.5" in reply


def test_api_reply_still_explains_a_failure_with_its_reason() -> None:
    from voxos.api.routes.chat import _build_response
    from voxos.core.task_manager.models import TaskStatus

    task = type(
        "FailedTask",
        (),
        {
            "status": TaskStatus.FAILED,
            "result": None,
            "error_info": {"reason": "Verification failed"},
            "intent": "open_application",
        },
    )()

    assert "Verification failed" in _build_response(task, "open your queue")


def test_api_reply_still_asks_for_approval_when_one_is_pending() -> None:
    from voxos.api.routes.chat import _build_response
    from voxos.core.task_manager.models import TaskStatus

    task = type(
        "PendingTask",
        (),
        {
            "status": TaskStatus.WAITING_FOR_APPROVAL,
            "result": None,
            "error_info": None,
            "intent": "delete_file",
        },
    )()

    assert "approval" in _build_response(task, "delete everything").lower()


@pytest.mark.asyncio
async def test_voice_bridge_speaks_the_reply_from_its_responder() -> None:
    from voxos.voice.bridge import VoxBridge

    async def responder(text: str, session_id: str) -> str:
        assert session_id == "session-1"
        return f"handled: {text}"

    bridge = VoxBridge(session_id="session-1", responder=responder)

    assert await bridge._reply_for("what time is it") == "handled: what time is it"


@pytest.mark.asyncio
async def test_voice_bridge_defaults_to_the_conversation_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Small talk must not be forced through the task pipeline on the voice path."""
    from voxos.core.conversation.models import AssistantReply
    from voxos.voice import bridge as bridge_module
    from voxos.voice.bridge import VoxBridge

    seen: list[str] = []

    class StubService:
        async def handle_text(self, text: str, session_id: str) -> AssistantReply:
            seen.append(text)
            return AssistantReply(text="I'm doing well, thanks.", kind="info")

    monkeypatch.setattr(bridge_module, "_build_conversation_service", lambda: StubService())

    bridge = VoxBridge(session_id="session-2")

    assert await bridge._reply_for("how are you?") == "I'm doing well, thanks."
    assert seen == ["how are you?"]
