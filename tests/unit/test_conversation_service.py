"""Tests for the UI-facing conversation reply boundary."""

from __future__ import annotations

import uuid

import pytest


class _History:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str]] = []

    async def append_turn(self, _session_id: str, *, role: str, content: str) -> None:
        self.turns.append((role, content))

    async def list_recent(self, _session_id: str, limit: int = 12):
        return []


class _TaskManager:
    def __init__(self) -> None:
        self.was_called = False

    async def submit_task(self, **_kwargs):
        self.was_called = True
        return object()

    async def run_pipeline(self, **_kwargs):
        return type(
            "CompletedTask",
            (),
            {
                "result": {
                    "step_1": {
                        "status": "SUCCESS",
                        "data": {"status": "Launched notepad", "app_name": "notepad"},
                    }
                },
                "error_info": None,
            },
        )()


class _ExecutionListener:
    async def process(self, _input):
        from voxos.agents.listener.agent import ListenerOutput

        return ListenerOutput(intent="open_application", requires_execution=True)


class _ChatListener:
    async def process(self, _input):
        from voxos.agents.listener.agent import ListenerOutput

        return ListenerOutput(intent="general_conversation", requires_execution=False)


@pytest.mark.asyncio
async def test_greeting_returns_human_text_without_running_a_task() -> None:
    from voxos.core.conversation.service import ConversationService

    history = _History()
    task_manager = _TaskManager()
    service = ConversationService(task_manager=task_manager, history=history)

    reply = await service.handle_text("hi", str(uuid.uuid4()))

    assert reply.kind == "info"
    assert "how can" in reply.text.lower()
    assert "{" not in reply.text
    assert task_manager.was_called is False
    assert history.turns == [("user", "hi"), ("assistant", reply.text)]


@pytest.mark.asyncio
async def test_successful_tool_result_is_never_rendered_as_a_dict() -> None:
    from voxos.core.conversation.service import ConversationService

    history = _History()
    service = ConversationService(
        task_manager=_TaskManager(),
        history=history,
        listener=_ExecutionListener(),
        strategist=object(),
        executor=object(),
        overseer=object(),
        permissions=object(),
    )

    reply = await service.handle_text("open notepad", str(uuid.uuid4()))

    assert reply.kind == "success"
    assert reply.text == "Opened Notepad."
    assert "{" not in reply.text
    assert history.turns[-1] == ("assistant", "Opened Notepad.")


@pytest.mark.asyncio
async def test_chat_reply_receives_recent_session_context() -> None:
    from voxos.core.conversation.service import ConversationService

    class HistoryWithContext(_History):
        async def list_recent(self, _session_id: str, limit: int = 12):
            assert limit == 12
            return [
                type("Turn", (), {"role": "user", "content": "My name is Abhijeet"})(),
                type("Turn", (), {"role": "assistant", "content": "Nice to meet you, Abhijeet."})(),
            ]

    seen_context = []

    async def responder(_text: str, context: list[dict[str, str]]) -> str:
        seen_context.extend(context)
        return "Your name is Abhijeet."

    history = HistoryWithContext()
    service = ConversationService(
        task_manager=_TaskManager(),
        history=history,
        listener=_ChatListener(),
        chat_responder=responder,
    )

    reply = await service.handle_text("What is my name?", str(uuid.uuid4()))

    assert reply.text == "Your name is Abhijeet."
    assert reply.kind == "info"
    assert seen_context[0]["content"] == "My name is Abhijeet"
    assert history.turns[-1] == ("assistant", "Your name is Abhijeet.")


@pytest.mark.asyncio
async def test_default_chat_responder_uses_stored_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.core.conversation import service as service_module
    from voxos.core.conversation.service import ConversationService
    from voxos.llm.base import LLMResponse

    class HistoryWithContext(_History):
        async def list_recent(self, _session_id: str, limit: int = 12):
            return [type("Turn", (), {"role": "user", "content": "Call me Abhijeet"})()]

    captured = []

    class Provider:
        async def complete(self, messages, _config):
            captured.extend(messages)
            return LLMResponse(content="Of course, Abhijeet.", model="test")

    class Router:
        provider = Provider()

        def config_for(self, **_kwargs):
            return object()

    monkeypatch.setattr(service_module, "get_llm_router", lambda: Router())
    service = ConversationService(task_manager=_TaskManager(), history=HistoryWithContext())

    reply = await service.handle_text("What should you call me?", str(uuid.uuid4()))

    assert reply.text == "Of course, Abhijeet."
    assert [message.content for message in captured] == [
        "Call me Abhijeet",
        "What should you call me?",
    ]


@pytest.mark.asyncio
async def test_history_outage_does_not_prevent_a_safe_greeting() -> None:
    from voxos.core.conversation.service import ConversationService

    class BrokenHistory:
        async def append_turn(self, *_args, **_kwargs):
            raise ConnectionError("database unavailable")

        async def list_recent(self, *_args, **_kwargs):
            raise ConnectionError("database unavailable")

    reply = await ConversationService(
        task_manager=_TaskManager(),
        history=BrokenHistory(),
    ).handle_text("hi", str(uuid.uuid4()))

    assert reply.text == "Hi — how can I help?"


def test_browser_open_result_has_a_human_action_reply() -> None:
    from voxos.core.conversation.service import ConversationService

    task = type(
        "CompletedTask",
        (),
        {
            "result": {"step_1": {"data": {"title": "New Tab", "url": "about:blank"}}},
            "error_info": None,
            "intent": "open_browser",
        },
    )()

    reply = ConversationService._reply_from_task(task)

    assert reply.text == "Opened your browser."
    assert reply.kind == "success"


def test_google_captcha_failure_has_a_safe_actionable_reply() -> None:
    from voxos.core.conversation.service import ConversationService

    task = type(
        "FailedTask",
        (),
        {
            "result": None,
            "error_info": {"failures": ["Google blocked this automated search. Try again with Bing."]},
            "intent": "search_web_browser",
        },
    )()

    reply = ConversationService._reply_from_task(task)

    assert reply.text == "Google blocked that automated search. Try searching with Bing instead."
    assert reply.kind == "error"
    assert "{" not in reply.text
