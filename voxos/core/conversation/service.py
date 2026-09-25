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
        status = getattr(task, "status", None)
        status_name = str(getattr(status, "value", status) or "")

        if status_name == "WAITING_FOR_APPROVAL":
            return AssistantReply(
                text="That needs your approval before I run it. Check the approval request.",
                kind="info",
            )

        # A multi-agent run already wrote one reply covering every part,
        # including which parts failed and why.
        result = getattr(task, "result", None)
        if isinstance(result, dict) and isinstance(result.get("reply"), str) and result["reply"].strip():
            return AssistantReply(
                text=result["reply"].strip(),
                kind="error" if status_name == "FAILED" else "success",
            )

        if task.error_info:
            failures = [str(failure) for failure in task.error_info.get("failures", [])]
            reason = str(task.error_info.get("reason", ""))
            blob = " ".join(failures + [reason])

            if "Google blocked" in blob:
                return AssistantReply(
                    text="Google blocked that automated search. Try searching with Bing instead.",
                    kind="error",
                    action="search",
                )
            if "blocked by policy" in blob or "critical risk" in blob:
                return AssistantReply(
                    text=(
                        "I blocked that — it matches a critical-risk pattern, "
                        "so I'm not allowed to run it."
                    ),
                    kind="error",
                )
            if "denied by the sandbox" in blob:
                return AssistantReply(
                    text=(
                        "That path is outside the folders I'm allowed to touch, "
                        "so I stopped before doing anything."
                    ),
                    kind="error",
                )
            return AssistantReply(
                text="I couldn’t complete that request. Please try again.",
                kind="error",
            )

        if status_name == "FAILED":
            return AssistantReply(
                text="I couldn’t complete that request. Please try again.",
                kind="error",
            )

        intent = getattr(task, "intent", None)
        if intent == "open_browser":
            return AssistantReply(text="Opened your browser.", kind="success", action="open")
        if intent == "open_youtube":
            return AssistantReply(text="Opened YouTube.", kind="success", action="open")

        sentences: list[str] = []
        action: str | None = None
        for data in ConversationService._result_data_steps(task.result):
            rendered = ConversationService._sentence_for(data, intent)
            if rendered is None:
                continue
            text, step_action = rendered
            if text not in sentences:
                sentences.append(text)
            action = action or step_action

        if not sentences:
            return AssistantReply(text="Done.", kind="success")
        return AssistantReply(text=" ".join(sentences), kind="success", action=action)

    @staticmethod
    def _sentence_for(
        data: dict[str, Any], intent: str | None = None
    ) -> tuple[str, str | None] | None:
        """Turn one tool's result payload into a sentence a person can read."""
        status = str(data.get("status", "")).strip()
        app_name = str(data.get("app_name", "")).strip()
        if status.lower().startswith("launched") or app_name:
            name = app_name or status.removeprefix("Launched ").strip()
            return f"Opened {name.title()}.", "open"

        engine, query = data.get("engine"), data.get("query")
        if engine and query:
            return f"Searched {str(engine).title()} for {query}.", "search"

        if "formatted" in data or ("hour" in data and "minute" in data):
            return ConversationService._time_sentence(data), None

        if isinstance(data.get("os"), dict):
            os_info = data["os"]
            host = data.get("host") if isinstance(data.get("host"), dict) else {}
            name = " ".join(
                part for part in (os_info.get("system"), str(os_info.get("release", ""))) if part
            ).strip()
            hostname = (host or {}).get("hostname")
            sentence = f"You're running {name}" if name else "Here are your system details"
            if hostname:
                sentence += f" on {hostname}"
            uptime = (host or {}).get("uptime_human")
            if uptime:
                sentence += f", up for {uptime}"
            return sentence + ".", None

        if isinstance(data.get("cpu"), dict) or isinstance(data.get("memory"), dict):
            cpu = data.get("cpu") or {}
            memory = data.get("memory") or {}
            parts = []
            if cpu.get("percent") is not None:
                parts.append(f"CPU is at {cpu['percent']}%")
            if memory.get("percent") is not None:
                used, total = memory.get("used_gb"), memory.get("total_gb")
                detail = f" ({used} of {total} GB used)" if used and total else ""
                parts.append(f"memory at {memory['percent']}%{detail}")
            disk = data.get("disk") or {}
            if disk.get("percent") is not None:
                parts.append(f"disk at {disk['percent']}%")
            return " and ".join(parts) + "." if parts else None, None

        if "content" in data:
            body = ConversationService._shorten(str(data["content"]).strip())
            name = ConversationService._base_name(data.get("path"))
            prefix = f"{name} contains: " if name else "It says: "
            return prefix + body, None

        if isinstance(data.get("items"), list):
            items = [
                entry.get("name", "") if isinstance(entry, dict) else entry
                for entry in data["items"]
            ]
            return ConversationService._listing_sentence(
                items, data.get("count"), "item", ConversationService._base_name(data.get("path"))
            ), None

        if isinstance(data.get("results"), list):
            results = data["results"]
            if results and isinstance(results[0], dict) and "name" in results[0]:
                names = [str(entry.get("name", "")) for entry in results]
                return ConversationService._listing_sentence(
                    names, data.get("count"), "match", None
                ), None
            if results and isinstance(results[0], dict):
                return ConversationService._web_result_sentence(results), None
            return ConversationService._listing_sentence(
                results, data.get("count"), "match", None
            ), None

        if isinstance(data.get("windows"), list):
            return ConversationService._listing_sentence(
                data["windows"], data.get("count"), "window open", None
            ), None

        if data.get("bytes_written") is not None:
            name = ConversationService._base_name(data.get("path")) or "the file"
            return f"Saved {name} ({data['bytes_written']} bytes).", None

        if data.get("created") and data.get("path"):
            return f"Created folder {ConversationService._base_name(data['path'])}.", None

        if data.get("source") and data.get("destination"):
            verb = "Moved" if intent and "move" in intent.lower() else "Copied"
            source = ConversationService._base_name(data["source"])
            destination = ConversationService._base_name(data["destination"])
            return f"{verb} {source} to {destination}.", None

        if data.get("old_path") and data.get("new_path"):
            old = ConversationService._base_name(data["old_path"])
            new = ConversationService._base_name(data["new_path"])
            return f"Renamed {old} to {new}.", None

        if data.get("deleted_path"):
            return f"Deleted {ConversationService._base_name(data['deleted_path'])}.", None

        return None

    @staticmethod
    def _time_sentence(data: dict[str, Any]) -> str:
        hour, minute = data.get("hour"), data.get("minute")
        if hour is None or minute is None:
            return f"It's {data.get('formatted', '')}".strip() + "."
        suffix = "AM" if int(hour) < 12 else "PM"
        display_hour = int(hour) % 12 or 12
        clock = f"{display_hour}:{int(minute):02d} {suffix}"
        weekday = data.get("weekday")
        return f"It's {clock} on {weekday}." if weekday else f"It's {clock}."

    @staticmethod
    def _web_result_sentence(results: list[dict[str, Any]]) -> str:
        """Render a web-search hit as speech, never as its raw payload."""
        first = results[0]
        title = str(first.get("title", "")).strip()
        body = str(first.get("snippet") or first.get("body") or "").strip()
        if "search failed" in title.lower():
            return f"My web search failed: {body or 'no detail was given'}."
        if not body:
            return f"I found {title}." if title else "I found some results."
        return "I found this: " + ConversationService._shorten(body)

    @staticmethod
    def _listing_sentence(
        values: list[Any], count: Any, noun: str, location: str | None
    ) -> str:
        total = count if count is not None else len(values)
        shown = ", ".join(str(value) for value in values[:10])
        if len(values) > 10:
            shown += ", …"
        where = f" in {location}" if location else ""
        plural = "" if total == 1 else ("es" if noun.endswith(("s", "x", "ch", "sh")) else "s")
        if not shown:
            return f"No {noun}{plural} found{where}."
        return f"{total} {noun}{plural}{where}: {shown}."

    @staticmethod
    def _shorten(text: str, limit: int = 400) -> str:
        collapsed = " ".join(text.lstrip("﻿").split())
        return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + " …"

    @staticmethod
    def _base_name(path: Any) -> str:
        if not path:
            return ""
        return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _result_data_steps(result: Any) -> list[dict[str, Any]]:
        if not isinstance(result, dict):
            return []
        return [
            step["data"]
            for step in result.values()
            if isinstance(step, dict) and isinstance(step.get("data"), dict)
        ]

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
