"""Regression tests for the highest-impact VoxOS defects."""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest


def test_fallback_plan_uses_registered_tool_names() -> None:
    from voxos.agents.strategist.planner import build_fallback_plan

    list_plan = build_fallback_plan("list_files", {"directory": "."})
    read_plan = build_fallback_plan("read_file", {"file_path": "notes.txt"})

    assert list_plan.steps[0].tool == "os.list_dir"
    assert list_plan.steps[0].arguments == {"dir_path": "."}
    assert read_plan.steps[0].tool == "os.read_file"
    assert read_plan.steps[0].arguments == {"file_path": "notes.txt"}


@pytest.mark.asyncio
async def test_chat_awaits_async_task_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.api.routes import chat as chat_route
    from voxos.core.task_manager.models import ChatRequest, Task, TaskStatus

    task = Task(session_id="session-1", user_request="hello", status=TaskStatus.COMPLETED)

    class FakeManager:
        async def create_task(self, **kwargs):
            return task

        async def run_pipeline(self, **kwargs):
            assert kwargs["task"] is task
            return task

    monkeypatch.setattr(chat_route, "get_task_manager", lambda: FakeManager())
    monkeypatch.setattr(chat_route, "_get_agents", lambda: (None, None, None, None))

    response = await chat_route.chat(ChatRequest(message="hello", session_id="session-1"))

    assert response.task_id == task.id
    assert response.task_status == TaskStatus.COMPLETED.value


def test_jwt_round_trip_works(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.config import get_settings
    from voxos.security.auth import create_access_token, verify_token

    get_settings.cache_clear()
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    get_settings.cache_clear()

    token = create_access_token({"sub": "user-1"}, timedelta(minutes=5))

    assert verify_token(token)["sub"] == "user-1"


def test_settings_loads_env_filename(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from voxos.config import get_settings

    monkeypatch.chdir(tmp_path)
    (tmp_path / "env").write_text(
        "JARVIS_PORT=9123\nPOSTGRES_HOST=db.example\nLLM_PROVIDER=local\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JARVIS_PORT", raising=False)
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.port == 9123
    assert settings.db.host == "db.example"
    assert settings.llm.provider == "local"
    get_settings.cache_clear()


def test_neon_postgres_url_is_normalized_for_asyncpg() -> None:
    from voxos.config import DatabaseSettings

    settings = DatabaseSettings(
        POSTGRES_URL=(
            "postgresql://user:password@db.example/neondb?"
            "sslmode=require&channel_binding=require"
        )
    )

    assert settings.url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in settings.url
    assert "sslmode" not in settings.url
    assert "channel_binding" not in settings.url


def test_redis_settings_supports_hosted_tls_url() -> None:
    from voxos.config import RedisSettings

    settings = RedisSettings(
        REDIS_URL="rediss://default:password@redis.example:6380/0"
    )

    assert settings.url == "rediss://default:password@redis.example:6380/0"


def test_shell_command_critical_pattern_is_blocked() -> None:
    from voxos.core.risk_engine.engine import RiskEngine
    from voxos.tools.os.shell import ShellCommandTool

    decision = RiskEngine().evaluate_tool(
        ShellCommandTool(),
        {"command": "rm -rf /"},
    )

    assert decision.is_blocked is True


@pytest.mark.asyncio
async def test_missing_file_returns_not_found_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from voxos.tools.os import fs
    from voxos.tools.os.fs import ReadFileArgs, ReadFileTool
    from voxos.tools.base import ToolStatus

    missing_path = Path("missing.txt")
    monkeypatch.setattr(fs, "_resolve_safe_path", lambda _: missing_path)

    result = await ReadFileTool()._execute(ReadFileArgs(file_path="missing.txt"))

    assert result.status == ToolStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_event_bus_supports_global_subscribers() -> None:
    from voxos.core.events.bus import EventBus, VoxEvent
    from voxos.core.events.types import EventType

    received = []

    async def handler(event):
        received.append(event)

    bus = EventBus()
    bus.subscribe_all(handler)
    await bus.publish(VoxEvent(event_type=EventType.TASK_CREATED))

    assert len(received) == 1
    assert received[0].event_type == EventType.TASK_CREATED


@pytest.mark.asyncio
async def test_approval_response_resolves_gateway_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.api.routes import approvals as approvals_route
    from voxos.core.risk_engine.engine import ApprovalRequest
    from voxos.tools.base import RiskLevel
    from datetime import datetime, timezone

    approval = ApprovalRequest(
        id="approval-1",
        tool_name="desktop.open_app",
        arguments={},
        risk_level=RiskLevel.HIGH,
        requested_action="Execute desktop.open_app",
        reason="test",
        consequences=[],
        task_id="task-1",
        session_id="session-1",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.max.replace(tzinfo=timezone.utc),
    )

    class FakeEngine:
        async def process_approval_response(self, **kwargs):
            assert kwargs == {"approval_id": "approval-1", "approved": True}
            approval.status = "APPROVED"
            return approval

    class FakeGateway:
        def resolve(self, approval_id: str, approved: bool) -> bool:
            assert (approval_id, approved) == ("approval-1", True)
            return True

    monkeypatch.setattr(approvals_route, "get_risk_engine", lambda: FakeEngine())
    monkeypatch.setattr(approvals_route, "get_approval_gateway", lambda: FakeGateway())

    response = await approvals_route.respond_to_approval(
        "approval-1", approvals_route.ApprovalResponse(approved=True)
    )

    assert response["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_event_bus_redis_connect_has_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from voxos.config import RedisSettings
    from voxos.core.events import bus as bus_module

    class FakeRedis:
        kwargs = None

        def __init__(self, **kwargs):
            FakeRedis.kwargs = kwargs

        async def ping(self):
            raise ConnectionError("redis unavailable")

        async def close(self):
            pass

    monkeypatch.setattr(bus_module.aioredis, "Redis", FakeRedis)
    monkeypatch.setattr(
        bus_module,
        "get_settings",
        lambda: SimpleNamespace(
            redis=RedisSettings(
                REDIS_URL=None,
                REDIS_HOST="localhost",
                REDIS_PORT=6379,
            )
        ),
    )
    bus = bus_module.EventBus()

    with pytest.raises(ConnectionError):
        await bus.connect()

    assert FakeRedis.kwargs["socket_connect_timeout"] <= 2
    assert FakeRedis.kwargs["socket_timeout"] <= 2
    assert bus._redis is None


@pytest.mark.asyncio
async def test_event_bus_uses_hosted_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from voxos.config import RedisSettings
    from voxos.core.events import bus as bus_module

    captured = {}

    class FakeRedis:
        async def ping(self):
            return True

        async def close(self):
            pass

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeRedis()

    monkeypatch.setattr(bus_module.aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(
        bus_module,
        "get_settings",
        lambda: SimpleNamespace(
            redis=RedisSettings(
                REDIS_URL="rediss://default:password@redis.example:6380/0"
            )
        ),
    )

    bus = bus_module.EventBus()
    await bus.connect()
    await bus.disconnect()

    assert captured["url"] == "rediss://default:password@redis.example:6380/0"
    assert captured["kwargs"]["socket_connect_timeout"] <= 2
    assert captured["kwargs"]["socket_timeout"] <= 2
    assert captured["kwargs"]["ssl_ca_certs"]


@pytest.mark.asyncio
async def test_llm_health_check_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.llm.openai_compat import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider("http://localhost:1/v1", "")

    class FakeModels:
        async def list(self):
            raise ConnectionError("provider unavailable")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(provider, "_client", FakeClient())

    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_tool_runner_enforces_permissions() -> None:
    from voxos.agents.executor.runner import ToolRunner
    from voxos.core.permissions.manager import PermissionManager
    from voxos.core.task_manager.models import Task, TaskStep
    from voxos.tools.base import ToolStatus

    result = await ToolRunner().run_step(
        TaskStep(action="run shell", tool="os.shell_command", arguments={"command": "echo no"}),
        Task(session_id="session-1", user_request="test"),
        permissions=PermissionManager(set()),
    )

    assert result.status == ToolStatus.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_task_sync_creates_missing_database_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxos.core.task_manager import manager as manager_module
    from voxos.core.task_manager.models import Task

    session_id = uuid.uuid4()
    task = Task(session_id=str(session_id), user_request="test")
    calls: list[tuple[str, object]] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def commit(self):
            calls.append(("commit", None))

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    class FakeSessionRepository:
        def __init__(self, _session):
            pass

        async def get(self, value):
            calls.append(("session_get", value))
            return None

        async def create(self, payload):
            calls.append(("session_create", payload))

    class FakeTaskRepository:
        def __init__(self, _session):
            pass

        async def get(self, value):
            calls.append(("task_get", value))
            return None

        async def create(self, payload):
            calls.append(("task_create", payload))

    monkeypatch.setattr(manager_module, "get_session_factory", lambda: FakeFactory())
    monkeypatch.setattr(manager_module, "SessionRepository", FakeSessionRepository)
    monkeypatch.setattr(manager_module, "TaskRepository", FakeTaskRepository)

    await manager_module.TaskManager()._sync_task_to_db(task)

    assert calls[0] == ("session_get", session_id)
    assert calls[1] == ("session_create", {
        "id": session_id,
        "status": "active",
        "session_metadata": {},
    })
    assert any(name == "task_create" for name, _ in calls)
