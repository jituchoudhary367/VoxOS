"""Tests for durable, session-scoped conversation history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


class _Result:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    def scalars(self):
        return SimpleNamespace(all=lambda: self._messages)

    def scalar_one_or_none(self):
        return self._messages[0] if self._messages else None


class _Session:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.added: list[object] = []

    async def execute(self, _statement):
        return _Result(self.messages)

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        pass


@pytest.mark.asyncio
async def test_recent_turns_are_session_scoped_and_oldest_first() -> None:
    from voxos.database.repositories.conversation_repo import ConversationHistoryRepository

    session_id = uuid4()
    now = datetime.now(timezone.utc)
    newest_first = [
        SimpleNamespace(role="assistant", content="I will", created_at=now + timedelta(seconds=1)),
        SimpleNamespace(role="user", content="remember this", created_at=now),
    ]

    turns = await ConversationHistoryRepository(_Session(newest_first)).list_recent(
        session_id,
        limit=12,
    )

    assert [(turn.role, turn.content) for turn in turns] == [
        ("user", "remember this"),
        ("assistant", "I will"),
    ]


@pytest.mark.asyncio
async def test_append_turn_reuses_the_session_conversation() -> None:
    from voxos.database.repositories.conversation_repo import ConversationHistoryRepository

    conversation = SimpleNamespace(id=uuid4())
    session = _Session([conversation])

    turn = await ConversationHistoryRepository(session).append_turn(
        uuid4(),
        role="user",
        content="open chrome",
    )

    assert turn.conversation_id == conversation.id
    assert turn.role == "user"
    assert turn.content == "open chrome"
    assert session.added == [turn]


@pytest.mark.asyncio
async def test_database_history_adapter_commits_each_session_turn() -> None:
    from voxos.core.conversation.history import DatabaseConversationHistory

    calls: list[object] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            calls.append("commit")

    class Factory:
        def __call__(self):
            return Session()

    class Repository:
        def __init__(self, _session):
            pass

        async def append_turn(self, session_id, *, role, content):
            calls.append((session_id, role, content))

    class SessionRepository:
        def __init__(self, _session):
            pass

        async def get(self, value):
            calls.append(("get", value))
            return object()

    session_id = uuid4()
    history = DatabaseConversationHistory(
        session_factory=Factory(),
        repository_factory=Repository,
        session_repository_factory=SessionRepository,
    )

    await history.append_turn(str(session_id), role="assistant", content="Done.")

    assert calls == [
        ("get", session_id),
        (session_id, "assistant", "Done."),
        "commit",
    ]


@pytest.mark.asyncio
async def test_database_history_adapter_creates_a_missing_parent_session() -> None:
    from voxos.core.conversation.history import DatabaseConversationHistory

    calls: list[object] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            calls.append("commit")

    class Factory:
        def __call__(self):
            return Session()

    class SessionRepository:
        def __init__(self, _session):
            pass

        async def get(self, session_id):
            calls.append(("get", session_id))
            return None

        async def create(self, payload):
            calls.append(("create", payload))

    class ConversationRepository:
        def __init__(self, _session):
            pass

        async def append_turn(self, session_id, *, role, content):
            calls.append(("append", session_id, role, content))

    session_id = uuid4()
    history = DatabaseConversationHistory(
        session_factory=Factory(),
        repository_factory=ConversationRepository,
        session_repository_factory=SessionRepository,
    )

    await history.append_turn(str(session_id), role="user", content="hi")

    assert calls == [
        ("get", session_id),
        ("create", {"id": session_id, "status": "active", "session_metadata": {}}),
        "commit",
        ("append", session_id, "user", "hi"),
        "commit",
    ]


@pytest.mark.asyncio
async def test_database_history_adapter_survives_losing_the_session_insert_race() -> None:
    """The task manager inserts the same session row concurrently; the turn must still be saved."""
    from sqlalchemy.exc import IntegrityError

    from voxos.core.conversation.history import DatabaseConversationHistory

    calls: list[object] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class SessionRepository:
        def __init__(self, _session):
            pass

        async def get(self, _session_id):
            return None

        async def create(self, _payload):
            raise IntegrityError("INSERT INTO sessions", {}, Exception("sessions_pkey"))

    class ConversationRepository:
        def __init__(self, _session):
            pass

        async def append_turn(self, session_id, *, role, content):
            calls.append(("append", role, content))

    history = DatabaseConversationHistory(
        session_factory=lambda: Session(),
        repository_factory=ConversationRepository,
        session_repository_factory=SessionRepository,
    )

    await history.append_turn(str(uuid4()), role="user", content="hi")

    assert calls == ["rollback", ("append", "user", "hi"), "commit"]


@pytest.mark.asyncio
async def test_database_history_adapter_resolves_a_session_factory_provider() -> None:
    from voxos.core.conversation.history import DatabaseConversationHistory

    calls: list[str] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            calls.append("commit")

    class SessionMaker:
        def __call__(self):
            return Session()

    class SessionRepository:
        def __init__(self, _session):
            pass

        async def get(self, _session_id):
            return object()

    class ConversationRepository:
        def __init__(self, _session):
            pass

        async def append_turn(self, *_args, **_kwargs):
            calls.append("append")

    history = DatabaseConversationHistory(
        session_factory=lambda: SessionMaker(),
        repository_factory=ConversationRepository,
        session_repository_factory=SessionRepository,
    )

    await history.append_turn(str(uuid4()), role="user", content="hi")

    assert calls == ["append", "commit"]
