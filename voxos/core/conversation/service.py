"""Conversation boundary between the UI and the task pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from voxos.agents.listener.agent import ListenerInput
from voxos.core.conversation.models import AssistantReply
from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger

log = get_logger(__name__)

ChatResponder = Callable[[str, list[dict[str, str]]], Awaitable[str]]


class ConversationHistory(Protocol):
    async def append_turn(self, session_id: str, *, role: str, content: str) -> Any: ...

    async def list_recent(self, session_id: str, limit: int = 12) -> list[Any]: ...


class ConversationService:
    """Produce user-facing replies while keeping task payloads out of the UI."""

    def __init__(
        self,
        *,
        task_manager: Any,
        history: ConversationHistory | None = None,
        listener: Any | None = None,
        strategist: Any | None = None,
        executor: Any | None = None,
        overseer: Any | None = None,
        permissions: Any | None = None,
        chat_responder: ChatResponder | None = None,
    ) -> None:
        self._task_manager = task_manager
        self._history = history
        self._listener = listener
        self._strategist = strategist
        self._executor = executor
        self._overseer = overseer
        self._permissions = permissions
        self._chat_responder = chat_responder or self._llm_reply

    async def handle_text(self, text: str, session_id: str) -> AssistantReply:
        """Handle a user turn and return only safe presentation text."""
        clean_text = text.strip()
        await self._append_turn(session_id, role="user", content=clean_text)

        if clean_text.lower() in {"hi", "hello", "hey", "hey voxos", "hi voxos", "hello voxos"}:
            reply = AssistantReply(text="Hi — how can I help?", kind="info")
            await self._append_turn(session_id, role="assistant", content=reply.text)
            return reply

        try:
            if self._listener is not None:
                listener_output = await self._listener.process(
                    ListenerInput(
                        text=clean_text,
                        session_id=session_id,
                        conversation_history=await self._recent_context(session_id),
                    )
                )
                if listener_output.requires_execution and self._can_run_pipeline():
                    task = await self._task_manager.submit_task(
                        user_request=clean_text,
                        session_id=session_id,
                    )
                    completed_task = await self._task_manager.run_pipeline(
                        task=task,
                        listener=self._listener,
                        strategist=self._strategist,
                        executor=self._executor,
                        overseer=self._overseer,
                        permissions=self._permissions,
                    )
                    reply = self._reply_from_task(completed_task)
                    await self._append_turn(session_id, role="assistant", content=reply.text)
                    return reply

            response = await self._chat_responder(clean_text, await self._recent_context(session_id))
            reply = AssistantReply(text=response.strip() or "How can I help?", kind="info")
        except Exception as error:
            log.exception("conversation_turn_failed", error=str(error))
            reply = self._error_reply(error)
        await self._append_turn(session_id, role="assistant", content=reply.text)
        return reply

    def _can_run_pipeline(self) -> bool:
        return all(
            value is not None
            for value in (
                self._listener,
                self._strategist,
                self._executor,
                self._overseer,
                self._permissions,
            )
        )

    @staticmethod
    def _reply_from_task(task: Any) -> AssistantReply:
        if task.error_info:
            failures = task.error_info.get("failures", [])
            if any("Google blocked" in str(failure) for failure in failures):
                return AssistantReply(
                    text="Google blocked that automated search. Try searching with Bing instead.",
                    kind="error",
                    action="search",
                )
            return AssistantReply(
                text="I couldn’t complete that request. Please try again.",
                kind="error",
            )

        data = ConversationService._first_result_data(task.result)
        if data is None:
            return AssistantReply(text="Done.", kind="success")

        if getattr(task, "intent", None) == "open_browser":
            return AssistantReply(text="Opened your browser.", kind="success", action="open")
        if getattr(task, "intent", None) == "open_youtube":
            return AssistantReply(text="Opened YouTube.", kind="success", action="open")

        status = str(data.get("status", "")).strip()
        app_name = str(data.get("app_name", "")).strip()
        if status.lower().startswith("launched") or app_name:
            name = app_name or status.removeprefix("Launched ").strip()
            return AssistantReply(text=f"Opened {name.title()}.", kind="success", action="open")

        engine = data.get("engine")
        query = data.get("query")
        if engine and query:
            return AssistantReply(
                text=f"Searched {str(engine).title()} for {query}.",
                kind="success",
                action="search",
            )

        return AssistantReply(text="Done.", kind="success")

    @staticmethod
    def _first_result_data(result: Any) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        for step in result.values():
            if isinstance(step, dict) and isinstance(step.get("data"), dict):
                return step["data"]
        return None

    async def _recent_context(self, session_id: str) -> list[dict[str, str]]:
        if self._history is None:
            return []
        try:
            turns = await self._history.list_recent(session_id, limit=12)
        except Exception as error:
            log.warning("conversation_history_read_failed", error=str(error))
            return []
        return [
            {"role": str(turn.role), "content": str(turn.content)}
            for turn in turns
            if getattr(turn, "role", None) and getattr(turn, "content", None)
        ]

    async def _llm_reply(self, text: str, context: list[dict[str, str]]) -> str:
        router = get_llm_router()
        messages = [LLMMessage(role=item["role"], content=item["content"]) for item in context]
        if not messages or messages[-1].content != text:
            messages.append(LLMMessage(role="user", content=text))
        config = router.config_for(
            complexity=TaskComplexity.SIMPLE,
            temperature=0.4,
            max_tokens=300,
            system_prompt=(
                "You are VoxOS, a helpful desktop assistant. Reply naturally and concisely. "
                "Do not return JSON, internal task data, Markdown links, or implementation details."
            ),
        )
        response = await router.provider.complete(messages, config)
        return response.content

    @staticmethod
    def _error_reply(_error: Exception) -> AssistantReply:
        return AssistantReply(
            text="I’m having trouble reaching the language service. Please try again in a moment.",
            kind="error",
        )

    async def _append_turn(self, session_id: str, *, role: str, content: str) -> None:
        if self._history is not None:
            try:
                await self._history.append_turn(session_id, role=role, content=content)
            except Exception as error:
                log.warning("conversation_history_write_failed", error=str(error))
