# VoxOS Conversation Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw task JSON in the VoxOS UI with durable, contextual assistant replies while making common browser and desktop interactions reliable.

**Architecture:** `ConversationService` owns UI turn handling and returns `AssistantReply`. It records PostgreSQL conversation turns and loads a capped per-session prompt context. TaskManager remains the security-aware tool executor, while the router and Playwright manager handle deterministic aliases and recoverable browser state.

**Tech Stack:** Python 3.13, FastAPI/async SQLAlchemy, Pydantic v2, PySide6/qasync, Playwright, Alembic, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-voxos-conversation-runtime-design.md`

## Global Constraints

- Preserve the current permission, risk, approval, task, audit, and event-bus paths.
- Never display raw exceptions, task dictionaries, or provider payloads in the UI.
- Store at most the latest 12 turns as direct prompt context; generate summaries only when a session exceeds that cap.
- Keep Google only for explicit requests; default automated assistant search to Bing.
- Treat missing wake-word dependencies as degraded optional functionality, not an app-start failure.

---

### Task 1: Durable conversation turns

**Files:**
- Create: `voxos/database/models/conversation.py`
- Create: `voxos/database/repositories/conversation_repo.py`
- Modify: `voxos/database/models/__init__.py`
- Create: `alembic/versions/0002_conversation_turns.py`
- Create: `tests/unit/test_conversation_repository.py`

**Interfaces:**
- Produces `ConversationTurn(session_id: UUID, role: str, content: str, created_at: datetime)`.
- Produces `ConversationRepository.append_turn(...)` and `list_recent(session_id, limit=12)`.

- [x] **Step 1: Write failing repository tests**

```python
async def test_recent_turns_are_session_scoped_and_oldest_first(session):
    repo = ConversationRepository(session)
    await repo.append_turn(session_id, "user", "remember this")
    await repo.append_turn(session_id, "assistant", "I will")
    turns = await repo.list_recent(session_id, limit=12)
    assert [(turn.role, turn.content) for turn in turns] == [
        ("user", "remember this"), ("assistant", "I will"),
    ]
```

- [x] **Step 2: Run the new repository test and confirm missing-model/repository failure.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_conversation_repository.py -q`

- [x] **Step 3: Add repository helpers and a database adapter using the existing `sessions → conversations → messages` schema; no duplicate model or migration was needed.**

```python
class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

- [x] **Step 4: Re-run repository tests and confirm the existing Alembic head (`0001_initial_schema`) is reachable.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_conversation_repository.py -q`

### Task 2: Conversation reply boundary

**Files:**
- Create: `voxos/core/conversation/service.py`
- Create: `voxos/core/conversation/models.py`
- Create: `tests/unit/test_conversation_service.py`

**Interfaces:**
- Produces `AssistantReply(text: str, kind: Literal["success", "error", "info"], action: str | None = None)`.
- Produces `ConversationService.handle_text(text, session_id) -> AssistantReply`.

- [x] **Step 1: Write failing tests for local greeting and task-result formatting.**

```python
async def test_greeting_returns_human_text_without_running_a_task():
    reply = await service.handle_text("hi", session_id)
    assert reply.kind == "info"
    assert "how can" in reply.text.lower()

async def test_successful_tool_result_is_never_rendered_as_a_dict():
    reply = await service.handle_text("open notepad", session_id)
    assert reply.kind == "success"
    assert reply.text == "Opened Notepad."
    assert "{" not in reply.text
```

- [x] **Step 2: Run the service tests and confirm they fail because the service does not exist.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_conversation_service.py -q`

- [x] **Step 3: Implement `AssistantReply` and `ConversationService`.**

```python
async def handle_text(self, text: str, session_id: str) -> AssistantReply:
    if self._is_greeting(text):
        return AssistantReply(text="Hi — how can I help?", kind="info")
    task = await self._run_task(text, session_id)
    return self._reply_from_task(task)
```

- [x] **Step 4: Persist both user and assistant turns; pass formatted recent turns to conversational prompts.**

- [x] **Step 5: Re-run service tests.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_conversation_service.py -q`

### Task 3: UI integration and safe errors

**Files:**
- Modify: `voxos/ui/app.py`
- Modify: `voxos/ui/dashboard.py`
- Modify: `voxos/llm/router.py`
- Create: `tests/unit/test_ui_reply_formatting.py`

**Interfaces:**
- `JarvisApp._run_chat_task` emits `reply.text` only.
- Provider failures map to `AssistantReply(kind="error")` with no exception internals.

- [x] **Step 1: Write failing UI formatter tests.**

```python
def test_provider_configuration_error_has_safe_message():
    reply = service._error_reply(NotFoundError("model_not_found"))
    assert "model_not_found" not in reply.text
    assert "language service" in reply.text.lower()
```

- [x] **Step 2: Run formatter tests and confirm failure.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_ui_reply_formatting.py -q`

- [x] **Step 3: Replace direct TaskManager rendering in `JarvisApp._run_chat_task` with `ConversationService`; retain detailed errors in structured logs only.**

- [x] **Step 4: Restrict `FallbackProvider` to `APIConnectionError` and `RateLimitError`; re-raise configuration/validation failures.**

- [x] **Step 5: Re-run formatter tests.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_ui_reply_formatting.py -q`

### Task 4: Deterministic browser and app intent routing

**Files:**
- Modify: `voxos/agents/listener/agent.py`
- Modify: `voxos/core/router.py`
- Modify: `voxos/tools/desktop/discovery.py`
- Create: `tests/unit/test_execution_router.py`

**Interfaces:**
- `open browser` emits an `open_browser` browser action.
- `open youtube` emits `open_youtube` and never accesses a nonexistent enum.
- `ExecutionRouter.route(...)` returns only `FAST_PATH`, `AUTOMATION`, or `REASONING`.

- [x] **Step 1: Write failing routing tests.**

```python
def test_open_youtube_routes_to_automation_without_llm():
    route = router.route("open_youtube", confidence=0.95, entities={})
    assert route is ExecutionClass.AUTOMATION

def test_open_browser_is_not_an_unknown_desktop_app():
    assert listener.fast_path("open browser").intent == "open_browser"
```

- [x] **Step 2: Run routing tests and confirm failure.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_execution_router.py -q`

- [x] **Step 3: Implement listener aliases and router/tool mappings.**

```python
AUTOMATION_INTENT_TO_TOOL = {
    "open_browser": "browser.open",
    "open_youtube": "browser.open",
    "search_web": "browser.search",
}
```

- [x] **Step 4: Re-run routing tests.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/unit/test_execution_router.py -q`

### Task 5: Browser recovery and search resilience

**Files:**
- Modify: `voxos/tools/browser/tools.py`
- Modify: `tests/tools/test_browser.py`

**Interfaces:**
- `_get_browser_session()` returns an open page backed by a connected browser.
- `BrowserSearchTool` defaults to `bing` and returns a friendly `SEARCH_BLOCKED` failure for Google CAPTCHA.

- [x] **Step 1: Write failing browser recovery tests.**

```python
async def test_closed_page_creates_a_fresh_session(monkeypatch):
    browser_tools._page_instance = closed_page
    page = await browser_tools._get_browser_session()
    assert page is fresh_page

async def test_default_search_engine_is_bing():
    result = await BrowserSearchTool().execute({"query": "whatsapp"})
    assert result.data["engine"] == "bing"
```

- [x] **Step 2: Run focused browser tests and confirm failure.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/tools/test_browser.py -q`

- [x] **Step 3: Check `page.is_closed()` and browser connection; clear singleton page/context state before recreating them. Convert known Google CAPTCHA URLs to `SEARCH_BLOCKED`.**

- [x] **Step 4: Re-run focused browser tests.**

Run: `python -m pytest -p pytest_asyncio.plugin tests/tools/test_browser.py -q`

### Task 6: End-to-end verification and wake-word status

**Files:**
- Modify: `voxos/ui/app.py`
- Modify: `tests/integration/test_agent_pipeline.py`
- Modify: `tests/unit/test_regressions.py`

- [x] **Step 1: Add regression coverage for greeting reply, contextual second turn, browser aliases, and a closed-page recovery path.**
- [x] **Step 2: Make missing wake-word dependencies emit a non-fatal UI status: `Wake word unavailable; text chat is ready.`**
- [x] **Step 3: Run the full verification suite.**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest -p pytest_asyncio.plugin -q`

- [x] **Step 4: Run compile and whitespace checks; pre-existing Ruff debt remains outside this change.**

Run: `python -m compileall -q voxos`
