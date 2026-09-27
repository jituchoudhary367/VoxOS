"""Vox learns who is using it: facts, preferences, habits — kept locally."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from voxos.core.profile.store import UserProfile


@pytest.fixture
def profile(tmp_path) -> UserProfile:
    return UserProfile(tmp_path / "profile.json")


def test_profile_has_a_stable_user_id(tmp_path):
    first = UserProfile(tmp_path / "profile.json")
    first.save()
    again = UserProfile(tmp_path / "profile.json")

    assert again.user_id == first.user_id
    assert len(first.user_id) == 36


def test_facts_are_deduplicated_and_newest_wins(profile):
    profile.add_facts(["Prefers HP laptops", "Lives in Pune"])
    profile.add_facts(["prefers hp laptops", "Budget for laptops is ₹50,000"])

    texts = [f["text"] for f in profile.facts]
    assert texts.count("Prefers HP laptops") + texts.count("prefers hp laptops") == 1
    assert texts[-1] == "Budget for laptops is ₹50,000"


def test_fact_list_is_capped(profile):
    profile.add_facts([f"fact {i}" for i in range(120)])

    assert len(profile.facts) == UserProfile.MAX_FACTS
    assert profile.facts[-1]["text"] == "fact 119"


def test_forget_removes_matching_facts(profile):
    profile.add_facts(["Prefers HP laptops", "Lives in Pune"])

    removed = profile.forget("hp")

    assert removed == 1
    assert [f["text"] for f in profile.facts] == ["Lives in Pune"]


def test_name_is_a_fact_with_its_own_slot(profile):
    profile.add_facts(["User's name is Abhijeet"])

    assert profile.name == "Abhijeet"


def test_activity_builds_habits(profile):
    profile.record_activity(tools=["browser.open", "browser.media"], sites=["www.youtube.com"], hour=23)
    profile.record_activity(tools=["browser.open"], sites=["youtube.com", "amazon.in"], hour=23)

    assert profile.habits["sites"]["youtube.com"] == 2
    assert profile.habits["tools"]["browser.open"] == 2
    assert profile.habits["hours"]["23"] == 2


def test_summary_is_short_and_useful(profile):
    profile.add_facts(["User's name is Abhijeet", "Prefers HP laptops"])
    for _ in range(3):
        profile.record_activity(tools=["browser.media"], sites=["youtube.com"], hour=23)

    text = profile.summary()

    assert "Abhijeet" in text
    assert "Prefers HP laptops" in text
    assert "youtube.com" in text
    assert "23" in text  # active around 23:00


def test_saved_file_is_plain_local_json(profile):
    profile.add_facts(["Prefers dark mode"])
    profile.save()

    data = json.loads(profile.path.read_text(encoding="utf-8"))
    assert data["facts"][0]["text"] == "Prefers dark mode"


def test_corrupt_file_starts_fresh_instead_of_crashing(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{not json", encoding="utf-8")

    profile = UserProfile(path)

    assert profile.facts == []
    assert profile.user_id


# ─── Learning from a conversation turn ──────────────────────────────────────

class ScriptedLLM:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, role, system, user, **_):
        self.prompts.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_learner_stores_durable_facts_and_forgets_corrections(profile):
    from voxos.core.profile.learner import learn_from_exchange

    profile.add_facts(["Prefers Dell laptops"])
    llm = ScriptedLLM({"facts": ["Prefers HP laptops", "  "], "forget": ["Dell"]})

    await learn_from_exchange(
        llm, profile,
        user_text="actually I prefer HP, not Dell",
        reply_text="Got it, HP it is.",
        vox_question="Which brand do you prefer?",
    )

    assert [f["text"] for f in profile.facts] == ["Prefers HP laptops"]
    assert "Which brand do you prefer?" in llm.prompts[0]


async def test_learner_failure_changes_nothing(profile):
    from voxos.core.profile.learner import learn_from_exchange

    profile.add_facts(["Lives in Pune"])
    await learn_from_exchange(ScriptedLLM(RuntimeError("down")), profile, user_text="hello there friend", reply_text="hi")

    assert [f["text"] for f in profile.facts] == ["Lives in Pune"]


@pytest.mark.parametrize(("text", "worth"), [
    ("pause", False),
    ("what time is it", False),
    ("my name is Abhijeet and I work on SAP", True),
    ("I usually listen to lofi while studying at night", True),
])
def test_learning_is_skipped_for_short_commands(text, worth):
    from voxos.core.profile.learner import worth_learning

    assert worth_learning(text) is worth


# ─── Habits from a finished task ────────────────────────────────────────────

def test_activity_is_read_from_the_task():
    from voxos.core.profile.learner import activity_from_task

    task = SimpleNamespace(
        result={"workers": [{"steps": [
            {"tool": "browser.open", "arguments": {"url": "https://www.amazon.in/s?k=hp"}, "status": "SUCCESS"},
            {"tool": "browser.media", "arguments": {"action": "pause"}, "status": "SUCCESS"},
        ]}]},
        plan=None,
    )

    tools, sites = activity_from_task(task)

    assert tools == ["browser.open", "browser.media"]
    assert sites == ["amazon.in"]


# ─── Wiring ─────────────────────────────────────────────────────────────────

async def test_orchestrator_recall_uses_the_profile(monkeypatch):
    from voxos.core.profile.store import get_profile
    from voxos.core.task_manager.manager import _default_orchestrator

    get_profile().add_facts(["Prefers HP laptops"])
    orchestrator = _default_orchestrator(object(), object())

    memories = await orchestrator._recall("any-session")

    assert "Prefers HP laptops" in memories[0]


async def test_chat_reply_knows_the_user(monkeypatch):
    from voxos.core.conversation import service as service_module
    from voxos.core.conversation.service import ConversationService
    from voxos.core.profile.store import get_profile
    from voxos.llm.base import LLMResponse

    get_profile().add_facts(["User's name is Abhijeet"])
    seen = {}

    class Provider:
        async def complete(self, messages, config):
            seen["system"] = config.system_prompt
            return LLMResponse(content="Hi Abhijeet!", model="m")

    class Router:
        provider = Provider()

        def config_for(self, **kwargs):
            from types import SimpleNamespace

            return SimpleNamespace(**kwargs)

    monkeypatch.setattr(service_module, "get_llm_router", lambda: Router())
    await ConversationService(task_manager=object())._llm_reply("who am I?", [])

    assert "Abhijeet" in seen["system"]


async def test_turns_update_the_profile_when_learning_is_on(monkeypatch):
    import voxos.core.profile.learner as learner

    monkeypatch.setenv("AGENT_LEARN_PROFILE", "true")
    calls = []
    monkeypatch.setattr(learner, "learn_in_background", lambda **kw: calls.append(kw))
    from voxos.core.conversation.service import ConversationService

    ConversationService._learn("I usually study with lofi music at night", "Noted.")

    assert calls and calls[0]["user_text"].startswith("I usually study")


def test_learning_is_off_in_tests_and_when_disabled():
    import voxos.core.profile.learner as learner
    from voxos.core.conversation.service import ConversationService

    # conftest sets AGENT_LEARN_PROFILE=false: no background LLM calls.
    ConversationService._learn("I usually study with lofi music at night", "Noted.")

    assert not learner._background
