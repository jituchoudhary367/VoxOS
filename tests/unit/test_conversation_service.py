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
    await service.flush()

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
    await service.flush()

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
    await service.flush()

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


def _task(*step_data: dict, intent: str | None = None):
    return type(
        "CompletedTask",
        (),
        {
            "result": {
                f"step_{index + 1}": {"status": "SUCCESS", "data": data}
                for index, data in enumerate(step_data)
            },
            "error_info": None,
            "intent": intent,
        },
    )()


def test_time_result_is_spoken_as_the_time() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "datetime": "2026-09-18T12:16:00+05:30",
            "formatted": "2026-09-18 12:16:00",
            "timezone": "Asia/Kolkata",
            "hour": 12,
            "minute": 16,
            "weekday": "Friday",
        })
    )

    assert reply.kind == "success"
    assert "12:16" in reply.text
    assert "Friday" in reply.text
    assert "{" not in reply.text


def test_system_info_result_names_the_operating_system() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "os": {"system": "Windows", "release": "11", "machine": "AMD64"},
            "host": {"hostname": "DESKTOP-ABC", "uptime_human": "3h 12m"},
            "python": {"version": "3.13.5"},
        })
    )

    assert "Windows" in reply.text
    assert "11" in reply.text
    assert "{" not in reply.text


def test_resource_result_reports_cpu_and_memory() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "cpu": {"percent": 21.7, "count_logical": 16},
            "memory": {"percent": 96.5, "used_gb": 15.1, "total_gb": 15.7},
            "disk": {"percent": 82.5, "free_gb": 120.0},
        })
    )

    assert "21.7" in reply.text
    assert "96.5" in reply.text
    assert "{" not in reply.text


def test_read_file_result_returns_the_contents() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"content": "buy milk", "path": "D:\\demo\\todo.txt"})
    )

    assert "buy milk" in reply.text
    assert "{" not in reply.text


def test_long_file_contents_are_truncated_for_the_ui() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"content": "x" * 5000, "path": "D:\\demo\\big.txt"})
    )

    assert len(reply.text) < 700
    assert "…" in reply.text


def test_directory_listing_reports_how_many_items() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"path": "D:\\demo", "items": ["a.txt", "b.txt"], "count": 2})
    )

    assert "2" in reply.text
    assert "a.txt" in reply.text
    assert "{" not in reply.text


def test_directory_listing_never_shows_raw_entry_payloads() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "path": "C:\\work\\demo",
            "items": [
                {"name": "app.py", "is_dir": False, "size_bytes": 16, "modified": "2026-09-18T13:23:35"},
                {"name": "notes.txt", "is_dir": False, "size_bytes": 13, "modified": "2026-09-18T13:23:35"},
            ],
            "count": 2,
        })
    )

    assert "app.py" in reply.text
    assert "notes.txt" in reply.text
    assert "{" not in reply.text
    assert "size_bytes" not in reply.text


def test_file_search_lists_the_file_names_it_found() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "results": [
                {"path": "C:\\work\\demo\\app.py", "name": "app.py", "size_bytes": 16},
                {"path": "C:\\work\\demo\\utils.py", "name": "utils.py", "size_bytes": 10},
            ],
            "count": 2,
            "root": "C:\\work\\demo",
        })
    )

    assert "app.py" in reply.text
    assert "utils.py" in reply.text
    assert "{" not in reply.text


def test_file_search_reports_the_matches() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"results": ["app.py", "utils.py"], "count": 2, "root": "D:\\demo"})
    )

    assert "2" in reply.text
    assert "app.py" in reply.text


def test_web_search_results_return_a_readable_snippet() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "results": [
                {"title": "What is SAP?", "snippet": "SAP is enterprise resource planning software."}
            ],
            "count": 1,
        })
    )

    assert "SAP is enterprise resource planning software." in reply.text
    assert "{" not in reply.text


def test_failed_web_search_explains_itself_instead_of_pretending() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({
            "results": [{"title": "Search Failed", "body": "Rate limit exceeded"}],
            "count": 1,
        })
    )

    assert "Rate limit exceeded" in reply.text
    assert "{" not in reply.text


def test_empty_web_search_says_nothing_was_found() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(_task({"results": [], "count": 0}))

    assert "no" in reply.text.lower() or "couldn't" in reply.text.lower()


def test_open_window_listing_reports_the_windows() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"windows": ["Notepad", "Chrome"], "count": 2})
    )

    assert "Notepad" in reply.text
    assert "{" not in reply.text


def test_written_file_is_confirmed_by_name() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"path": "D:\\demo\\todo.txt", "bytes_written": 8, "mode": "w"})
    )

    assert "todo.txt" in reply.text
    assert "{" not in reply.text


def test_created_folder_is_confirmed_by_name() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"path": "D:\\demo\\reports", "created": True})
    )

    assert "reports" in reply.text
    assert "{" not in reply.text


def test_copied_file_is_confirmed_with_both_names() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"source": "D:\\demo\\notes.txt", "destination": "D:\\demo\\notes_backup.txt"})
    )

    assert "notes.txt" in reply.text
    assert "notes_backup.txt" in reply.text


def test_renamed_file_is_confirmed_with_both_names() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"old_path": "D:\\demo\\report.md", "new_path": "D:\\demo\\final_report.md"})
    )

    assert "report.md" in reply.text
    assert "final_report.md" in reply.text


def test_deleted_file_is_confirmed_by_name() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task({"deleted_path": "D:\\demo\\old.txt"})
    )

    assert "old.txt" in reply.text


def test_every_step_of_a_parallel_task_is_reported() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _task(
            {"formatted": "2026-09-18 12:16:00", "hour": 12, "minute": 16, "weekday": "Friday"},
            {"os": {"system": "Windows", "release": "11"}, "host": {"hostname": "DESKTOP-ABC"}},
            {"cpu": {"percent": 21.7}, "memory": {"percent": 96.5, "used_gb": 15.1, "total_gb": 15.7}},
        )
    )

    assert "12:16" in reply.text
    assert "Windows" in reply.text
    assert "96.5" in reply.text


def _status_task(status: str, *, error_info=None, result=None, intent=None):
    return type(
        "StatusTask",
        (),
        {"status": status, "result": result, "error_info": error_info, "intent": intent},
    )()


def test_a_blocked_command_says_it_was_blocked_for_safety() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _status_task(
            "FAILED",
            error_info={
                "reason": "Blocked by policy",
                "failures": ["Command matches critical risk pattern and is blocked by policy"],
            },
        )
    )

    assert "blocked" in reply.text.lower()
    assert reply.kind == "error"
    assert "try again" not in reply.text.lower()


def test_a_sandbox_denial_explains_the_folder_limit() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(
        _status_task(
            "FAILED",
            error_info={
                "reason": "Max retries exceeded",
                "failures": ["Access to path 'D:\\demo' is denied by the sandbox."],
            },
        )
    )

    assert "allowed" in reply.text.lower() or "outside" in reply.text.lower()
    assert reply.kind == "error"


def test_a_task_waiting_for_approval_asks_for_approval() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(_status_task("WAITING_FOR_APPROVAL"))

    assert "approval" in reply.text.lower()
    assert reply.text != "Done."


def test_a_failed_task_never_claims_it_is_done() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(_status_task("FAILED", result={}, error_info=None))

    assert reply.text != "Done."
    assert reply.kind == "error"


def test_unrecognised_result_still_falls_back_to_done() -> None:
    from voxos.core.conversation.service import ConversationService

    reply = ConversationService._reply_from_task(_task({"mystery_key": "unknown shape"}))

    assert reply.text == "Done."
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


# ─── History must not sit on the reply path ─────────────────────────────────
# Reads and writes to the remote Postgres took 2-13 s per message.


class _SlowHistory(_History):
    def __init__(self, seed=None) -> None:
        super().__init__()
        self.release = __import__("asyncio").Event()
        self.seed = seed or []
        self.reads = 0

    async def append_turn(self, _session_id: str, *, role: str, content: str) -> None:
        await self.release.wait()
        self.turns.append((role, content))

    async def list_recent(self, _session_id: str, limit: int = 12):
        self.reads += 1
        return self.seed


@pytest.mark.asyncio
async def test_reply_does_not_wait_for_history_writes() -> None:
    import asyncio

    from voxos.core.conversation.service import ConversationService

    history = _SlowHistory()
    service = ConversationService(
        task_manager=_TaskManager(), history=history, listener=_ChatListener(),
        chat_responder=lambda _t, _c: asyncio.sleep(0, result="Sure."),
    )

    reply = await asyncio.wait_for(service.handle_text("tell me a joke", str(uuid.uuid4())), timeout=1)

    assert reply.text == "Sure."
    assert history.turns == []
    history.release.set()
    await service.flush()
    assert history.turns == [("user", "tell me a joke"), ("assistant", "Sure.")]


@pytest.mark.asyncio
async def test_context_comes_from_memory_after_the_first_read() -> None:
    import asyncio

    from voxos.core.conversation.service import ConversationService

    seed = [type("Turn", (), {"role": "user", "content": "My name is Abhijeet"})()]
    history = _SlowHistory(seed=seed)
    history.release.set()
    seen: list[list[str]] = []

    async def responder(_text, context):
        seen.append([c["content"] for c in context])
        return "ok"

    session = str(uuid.uuid4())
    for text in ("first", "second"):
        # A fresh service per message, the way the desktop UI calls it.
        service = ConversationService(
            task_manager=_TaskManager(), history=history, listener=_ChatListener(), chat_responder=responder,
        )
        await service.handle_text(text, session)

    assert history.reads == 1
    assert seen[1] == ["My name is Abhijeet", "first", "ok", "second"]


@pytest.mark.asyncio
async def test_slow_first_history_read_is_capped() -> None:
    import asyncio

    from voxos.core.conversation import service as service_module
    from voxos.core.conversation.service import ConversationService

    class HangingHistory(_History):
        async def list_recent(self, _session_id: str, limit: int = 12):
            await asyncio.sleep(30)
            return []

    service = ConversationService(
        task_manager=_TaskManager(), history=HangingHistory(), listener=_ChatListener(),
        chat_responder=lambda _t, _c: asyncio.sleep(0, result="ok"),
    )

    reply = await asyncio.wait_for(
        service.handle_text("hello there", str(uuid.uuid4())),
        timeout=service_module.HISTORY_READ_TIMEOUT_S + 1,
    )
    assert reply.text == "ok"


@pytest.mark.asyncio
async def test_pipeline_reuses_the_listener_result_instead_of_a_second_llm_call() -> None:
    from voxos.agents.listener.agent import ListenerInput, ListenerOutput
    from voxos.core.conversation.service import ConversationService

    class CountingListener:
        calls = 0

        async def process(self, _input):
            CountingListener.calls += 1
            return ListenerOutput(intent="open_application", requires_execution=True)

    class PipelineCallsListener(_TaskManager):
        async def run_pipeline(self, **kwargs):
            await kwargs["listener"].process(ListenerInput(text="open notepad", session_id="s"))
            return await super().run_pipeline(**kwargs)

    service = ConversationService(
        task_manager=PipelineCallsListener(), history=_History(), listener=CountingListener(),
        strategist=object(), executor=object(), overseer=object(), permissions=object(),
    )

    await service.handle_text("open notepad", str(uuid.uuid4()))

    assert CountingListener.calls == 1


@pytest.mark.asyncio
async def test_question_about_the_screen_looks_instead_of_chatting() -> None:
    """ "what is shown on the amazon page" was answered from memory."""
    from voxos.core.conversation.service import ConversationService

    manager = _TaskManager()

    async def responder(_text, _context):
        raise AssertionError("must not answer from memory")

    service = ConversationService(
        task_manager=manager, history=_History(), listener=_ChatListener(), chat_responder=responder,
        strategist=object(), executor=object(), overseer=object(), permissions=object(),
    )

    await service.handle_text("what is shown on the amazon page right now", str(uuid.uuid4()))

    assert manager.was_called


@pytest.mark.asyncio
async def test_plain_follow_up_chat_still_chats() -> None:
    from voxos.core.conversation.service import ConversationService

    manager = _TaskManager()

    async def responder(_text, _context):
        return "Because the command had no app name."

    service = ConversationService(
        task_manager=manager, history=_History(), listener=_ChatListener(), chat_responder=responder,
        strategist=object(), executor=object(), overseer=object(), permissions=object(),
    )

    reply = await service.handle_text("why", str(uuid.uuid4()))

    assert reply.text == "Because the command had no app name."
    assert manager.was_called is False
