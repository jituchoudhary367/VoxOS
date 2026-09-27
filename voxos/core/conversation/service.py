"""Conversation boundary between the UI and the task pipeline."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from voxos.agents.listener.agent import ListenerInput, ListenerOutput
from voxos.core.conversation import pending
from voxos.core.conversation.models import AssistantReply
from voxos.core.router import asks_about_screen
from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger

log = get_logger(__name__)

ChatResponder = Callable[[str, list[dict[str, str]]], Awaitable[str]]

# The history store is a remote Postgres: 2-13 s per message when awaited.
# Recent turns are therefore kept in memory per session (shared across
# service instances — the desktop UI builds one per message), loaded from the
# store once per session, and written back in the background.
HISTORY_READ_TIMEOUT_S = 1.5
CONTEXT_TURNS = 12
_SESSION_TURNS: dict[str, list[dict[str, str]]] = {}
_WRITE_TAILS: dict[str, asyncio.Task] = {}


_MEDIA_DONE = {
    "pause": "Paused", "stop": "Stopped", "play": "Playing", "toggle": "Toggled",
    "mute": "Muted", "unmute": "Unmuted", "next": "Skipped to the next video in",
}


def recent_turns(session_id: str, limit: int = CONTEXT_TURNS) -> list[dict[str, str]]:
    """This session's latest turns as held in memory (no database call)."""
    return [dict(turn) for turn in _SESSION_TURNS.get(session_id, [])[-limit:]]


class ConversationHistory(Protocol):
    async def append_turn(self, session_id: str, *, role: str, content: str) -> Any: ...

    async def list_recent(self, session_id: str, limit: int = 12) -> list[Any]: ...


class _Understood:
    """The Listener, answering the request it already understood from memory.

    The pipeline runs the Listener on the same text again; that second LLM
    call cost 0.5-0.8 s per command for the same answer.
    """

    def __init__(self, listener: Any, text: str, output: Any) -> None:
        self._listener, self._text, self._output = listener, text, output

    async def process(self, input: Any) -> Any:
        if getattr(input, "text", None) == self._text:
            return self._output
        return await self._listener.process(input)


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
            waiting = pending.get(session_id) if self._can_run_pipeline() else None
            if waiting is not None and not pending.looks_like_answer(clean_text, waiting.questions):
                # A new request instead of an answer: drop the question.
                pending.clear(session_id)
                waiting = None
            if waiting is not None:
                # Vox asked something last turn; this message is the answer
                # ("yes", "HP", "the second one"), not a chat line.
                pending.clear(session_id)
                request = pending.as_request(waiting, clean_text)
                understood = ListenerOutput(
                    intent="answer_to_question", requires_execution=True, confidence=1.0,
                    language="en", raw_text=request,
                )
                asked = " ".join(q["question"] for q in waiting.questions)
                return await self._run_task(
                    session_id, request, understood, user_text=clean_text, vox_question=asked,
                )

            if self._listener is not None:
                listener_output = await self._listener.process(
                    ListenerInput(
                        text=clean_text,
                        session_id=session_id,
                        conversation_history=await self._recent_context(session_id),
                    )
                )
                needs_action = listener_output.requires_execution or asks_about_screen(clean_text)
                if needs_action and self._can_run_pipeline():
                    return await self._run_task(session_id, clean_text, listener_output)

            response = await self._chat_responder(clean_text, await self._recent_context(session_id))
            reply = AssistantReply(text=response.strip() or "How can I help?", kind="info")
            self._learn(clean_text, reply.text)
        except Exception as error:
            log.exception("conversation_turn_failed", error=str(error))
            reply = self._error_reply(error)
        await self._append_turn(session_id, role="assistant", content=reply.text)
        return reply

    @staticmethod
    def _learn(user_text: str, reply_text: str, task: Any = None, vox_question: str | None = None) -> None:
        """Update the user profile after a turn (habits now, facts in the background)."""
        from voxos.config import get_settings

        if not get_settings().agent.learn_profile:
            return
        try:
            from voxos.core.profile.learner import learn_in_background

            learn_in_background(
                user_text=user_text, reply_text=reply_text, task=task, vox_question=vox_question,
            )
        except Exception as error:  # noqa: BLE001 — never cost the user a reply
            log.warning("profile_update_failed", error=str(error))

    async def _run_task(
        self,
        session_id: str,
        request: str,
        understood: Any,
        *,
        user_text: str | None = None,
        vox_question: str | None = None,
    ) -> AssistantReply:
        task = await self._task_manager.submit_task(user_request=request, session_id=session_id)
        completed_task = await self._task_manager.run_pipeline(
            task=task,
            listener=_Understood(self._listener, request, understood),
            strategist=self._strategist,
            executor=self._executor,
            overseer=self._overseer,
            permissions=self._permissions,
        )
        result = getattr(completed_task, "result", None)
        reply = self._reply_from_task(completed_task)
        if isinstance(result, dict) and result.get("questions"):
            pending.remember(session_id, request, result["questions"])
        elif reply.text.rstrip().endswith("?"):
            # An agent that finished with a question ("Shall I proceed to
            # checkout?") is waiting too; the "no" must come back to the
            # agents, not to chat (which claimed it "removed" the item).
            last = re.split(r"(?<=[.!])\s+", reply.text.strip())[-1]
            pending.remember(session_id, request, [{"question": last, "options": []}])
        self._learn(user_text or request, reply.text, completed_task, vox_question)
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

        if data.get("action") in _MEDIA_DONE and "media" in data:
            what = str(data.get("title") or "").strip()
            if data.get("tab") is None:  # sent to another app via the media keys
                return f"Sent the {data['action']} command to your media player.", "media"
            done = _MEDIA_DONE[data["action"]]
            return (f"{done} {what}." if what else f"{done} the video."), "media"

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
        turns = await self._session_turns(session_id)
        return [dict(turn) for turn in turns[-CONTEXT_TURNS:]]

    async def _session_turns(self, session_id: str) -> list[dict[str, str]]:
        """This session's turns, read from the store only the first time."""
        turns = _SESSION_TURNS.get(session_id)
        if turns is not None:
            return turns
        stored: list[Any] = []
        try:
            stored = await asyncio.wait_for(
                self._history.list_recent(session_id, limit=CONTEXT_TURNS),
                timeout=HISTORY_READ_TIMEOUT_S,
            )
        except Exception as error:  # includes the timeout
            log.warning("conversation_history_read_failed", error=str(error) or type(error).__name__)
        turns = _SESSION_TURNS.setdefault(session_id, [
            {"role": str(turn.role), "content": str(turn.content)}
            for turn in stored
            if getattr(turn, "role", None) and getattr(turn, "content", None)
        ])
        return turns

    async def _llm_reply(self, text: str, context: list[dict[str, str]]) -> str:
        router = get_llm_router()
        messages = [LLMMessage(role=item["role"], content=item["content"]) for item in context]
        if not messages or messages[-1].content != text:
            messages.append(LLMMessage(role="user", content=text))
        system_prompt = (
            "You are Vox, a helpful desktop voice assistant. Reply naturally and concisely. "
            "Do not return JSON, internal task data, Markdown links, or implementation details.\n"
            "In this reply you cannot take any action. Never say you did, removed, opened, bought, "
            "sent or changed anything unless the conversation shows it was already done; if the user "
            "wants something done, say you'll need them to ask for it as a command."
        )
        try:
            from voxos.core.profile.store import get_profile

            about = get_profile().summary()
        except Exception:  # noqa: BLE001 — chat works without the profile
            about = ""
        if about:
            system_prompt += f"\n\nWhat you know about the user (use it naturally, don't recite it):\n{about}"
        config = router.config_for(
            complexity=TaskComplexity.SIMPLE,
            temperature=0.4,
            max_tokens=300,
            system_prompt=system_prompt,
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
        """Record a turn in memory now and in the store in the background."""
        if self._history is None:
            return
        turns = await self._session_turns(session_id)
        turns.append({"role": role, "content": content})
        del turns[:-CONTEXT_TURNS * 4]

        history = self._history
        previous = _WRITE_TAILS.get(session_id)

        async def write() -> None:
            if previous is not None:  # keep this session's turns in order
                await asyncio.gather(previous, return_exceptions=True)
            try:
                await history.append_turn(session_id, role=role, content=content)
            except Exception as error:
                log.warning("conversation_history_write_failed", error=str(error))

        tail = asyncio.create_task(write())
        _WRITE_TAILS[session_id] = tail
        tail.add_done_callback(
            lambda done: _WRITE_TAILS.pop(session_id, None) if _WRITE_TAILS.get(session_id) is done else None
        )

    @staticmethod
    async def flush() -> None:
        """Wait until every pending history write has finished."""
        while _WRITE_TAILS:
            await asyncio.gather(*list(_WRITE_TAILS.values()), return_exceptions=True)
