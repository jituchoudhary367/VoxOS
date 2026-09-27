"""Vox asks when it must, remembers what it asked, and treats the reply as the answer.

Cases: an error with a workaround that changes the request ("Amazon is
blocked — search Flipkart instead?"), a choice with no sensible default,
and a step the user did not ask for that cannot be undone.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from voxos.orchestration.coordinator import SubGoal
from voxos.orchestration.graph import VoxOrchestrator
from voxos.orchestration.synthesizer import synthesize
from voxos.orchestration.worker import Worker, WorkerResult


class ScriptedLLM:
    def __init__(self, **scripts: list[Any]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.prompts: dict[str, list[str]] = {}

    async def ask(self, role: str, system: str, user: str, **_: Any) -> dict[str, Any]:
        self.prompts.setdefault(role, []).append(user)
        return self.scripts[role].pop(0)


class OkExecutor:
    async def execute_step(self, step, task, session_id, permissions, prior_results=None):
        return {"step_id": step.id, "status": "SUCCESS", "data": {"ok": True}}


class FailingExecutor:
    async def execute_step(self, step, task, session_id, permissions, prior_results=None):
        return {"step_id": step.id, "status": "FAILED", "error": "amazon.in blocked the request", "error_code": "BROWSER_ERROR"}


class Overseer:
    async def verify_step(self, step_result, plan_step):
        return {"passed": True}


ASK = {
    "action": "ask",
    "reason": "Amazon blocked the search",
    "questions": [
        {"question": "Amazon blocked the search. Should I search Flipkart instead?", "options": ["Yes, Flipkart", "No, stop"]},
    ],
}


# ─── Worker ─────────────────────────────────────────────────────────────────

async def test_worker_can_stop_and_ask():
    llm = ScriptedLLM(worker=[
        {"action": "tool", "tool": "browser.open", "arguments": {"url": "https://www.amazon.in/s?k=hp"}},
        ASK,
    ])

    result = await Worker(llm, FailingExecutor(), Overseer()).run(
        SubGoal(id="g1", goal="Search Amazon for HP laptops", tools=["browser"]), request="", session_id="s",
    )

    assert result.status == "needs_input"
    assert result.questions[0]["question"].startswith("Amazon blocked")
    assert result.questions[0]["options"] == ["Yes, Flipkart", "No, stop"]


async def test_worker_keeps_at_most_three_questions_and_drops_empty_ones():
    llm = ScriptedLLM(worker=[{
        "action": "ask",
        "questions": [{"question": f"Q{i}?"} for i in range(5)] + [{"question": "  "}, "plain string?"],
    }])

    result = await Worker(llm, OkExecutor(), Overseer()).run(
        SubGoal(id="g1", goal="x"), request="", session_id="s",
    )

    assert [q["question"] for q in result.questions] == ["Q0?", "Q1?", "Q2?"]


# ─── Graph + synthesizer ────────────────────────────────────────────────────

async def test_questions_from_parallel_workers_are_asked_together():
    llm = ScriptedLLM(
        coordinator=[{"goals": [
            {"id": "g1", "goal": "Search Amazon for laptops", "tools": ["browser"]},
            {"id": "g2", "goal": "Order the cheapest one", "tools": ["browser"]},
        ]}],
        worker=[
            {"action": "ask", "questions": [{"question": "Which brand do you prefer?", "options": ["HP", "Dell"]}]},
            {"action": "ask", "questions": [{"question": "Placing an order cannot be undone. Go ahead?", "options": ["Yes", "No"]}]},
        ],
    )

    outcome = await VoxOrchestrator(llm=llm, executor=OkExecutor(), overseer=Overseer()).run(
        "find a laptop and order the cheapest", session_id="s",
    )

    assert [q["question"] for q in outcome.questions] == [
        "Which brand do you prefer?", "Placing an order cannot be undone. Go ahead?",
    ]
    assert "1." in outcome.reply and "2." in outcome.reply
    assert "HP or Dell" in outcome.reply


async def test_single_question_reply_needs_no_llm_and_lists_options():
    reply = await synthesize("x", [WorkerResult(
        goal_id="g1", goal="x", status="needs_input",
        questions=[{"question": "Should I search Flipkart instead?", "options": ["Yes", "No"]}],
    )], ScriptedLLM())

    assert reply == "Should I search Flipkart instead? (Yes or No)"


async def test_done_parts_are_reported_before_the_questions():
    llm = ScriptedLLM()  # one finished part is spoken as is
    reply = await synthesize("x", [
        WorkerResult(goal_id="g1", goal="battery", status="done", answer="Battery 40%."),
        WorkerResult(goal_id="g2", goal="order", status="needs_input",
                     questions=[{"question": "Go ahead with the order?", "options": []}]),
    ], llm)

    assert reply.startswith("Battery 40%.")
    assert reply.endswith("Go ahead with the order?")


# ─── Conversation: the next message is the answer ───────────────────────────

class _TaskManager:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.requests: list[str] = []
        self.listener_intents: list[str] = []

    async def submit_task(self, user_request: str, session_id: str, **_):
        self.requests.append(user_request)
        return object()

    async def run_pipeline(self, **kwargs):
        from voxos.agents.listener.agent import ListenerInput

        out = await kwargs["listener"].process(ListenerInput(text=self.requests[-1], session_id="s"))
        self.listener_intents.append(out.intent)
        result = self.result
        return type("Task", (), {"result": result, "error_info": None, "status": "COMPLETED", "intent": out.intent})()


class _ChatListener:
    async def process(self, _input):
        from voxos.agents.listener.agent import ListenerOutput

        return ListenerOutput(intent="general_conversation", requires_execution=False)


class _ActionListener:
    async def process(self, _input):
        from voxos.agents.listener.agent import ListenerOutput

        return ListenerOutput(intent="search_web", requires_execution=True)


class _History:
    async def append_turn(self, *_a, **_k):
        return None

    async def list_recent(self, *_a, **_k):
        return []


def _service(manager, listener):
    from voxos.core.conversation.service import ConversationService

    return ConversationService(
        task_manager=manager, history=_History(), listener=listener,
        strategist=object(), executor=object(), overseer=object(), permissions=object(),
        chat_responder=lambda _t, _c: (_ for _ in ()).throw(AssertionError("answer must not go to chat")),
    )


async def test_reply_after_a_question_continues_the_original_request():
    from voxos.core.conversation import pending

    session = str(uuid.uuid4())
    asking = _TaskManager({"reply": "Amazon blocked it. Search Flipkart instead? (Yes or No)",
                           "questions": [{"question": "Search Flipkart instead?", "options": ["Yes", "No"]}]})
    await _service(asking, _ActionListener()).handle_text("search amazon for hp laptops", session)
    assert pending.get(session) is not None

    answering = _TaskManager({"reply": "Flipkart shows HP laptops."})
    reply = await _service(answering, _ChatListener()).handle_text("yes", session)

    assert reply.text == "Flipkart shows HP laptops."
    sent = answering.requests[0]
    assert "search amazon for hp laptops" in sent
    assert "Search Flipkart instead?" in sent and "yes" in sent
    assert answering.listener_intents == ["answer_to_question"]
    assert pending.get(session) is None


async def test_questions_expire(monkeypatch):
    from voxos.core.conversation import pending

    session = str(uuid.uuid4())
    pending.remember(session, "old request", [{"question": "Q?", "options": []}])
    real_now = pending._now
    monkeypatch.setattr(pending, "_now", lambda: real_now() + pending.TTL_S + 1)

    assert pending.get(session) is None


# ─── Irreversible clicks need the user's yes (live: "buy" put a laptop in the cart) ──

class _ClickPage:
    def __init__(self) -> None:
        self.clicked: list[str] = []

    def get_by_text(self, text, exact=False):
        page = self

        class Loc:
            @property
            def first(self):
                return self

            async def click(self, timeout=None):
                page.clicked.append(text)

        return Loc()


@pytest.fixture
def click_page(monkeypatch):
    from voxos.tools.browser import tools as browser

    page = _ClickPage()

    async def session(navigate=False):
        return page

    monkeypatch.setattr(browser, "_get_browser_session", session)
    return page


async def _click(text: str, request: str):
    from voxos.core.request_context import user_request
    from voxos.tools.browser.tools import BrowserClickArgs, BrowserClickTool

    with user_request(request):
        return await BrowserClickTool()._execute(BrowserClickArgs(text=text))


@pytest.mark.parametrize("button", ["Buy Now", "Place your order", "Proceed to Payment", "Pay ₹26,989", "Delete", "Send"])
async def test_irreversible_click_is_refused_without_a_yes(click_page, button):
    result = await _click(button, "buy the cheapest laptop on amazon")

    assert result.error_code == "NEEDS_CONFIRMATION"
    assert click_page.clicked == []


@pytest.mark.parametrize("button", ["Add to Cart", "See all results", "Next page", "Sign in"])
async def test_ordinary_clicks_are_not_blocked(click_page, button):
    result = await _click(button, "buy the cheapest laptop on amazon")

    assert result.success
    assert click_page.clicked == [button]


async def test_after_the_user_says_yes_the_click_goes_through(click_page):
    from voxos.core.conversation import pending

    item = pending.PendingQuestions(
        request="buy the cheapest laptop on amazon",
        questions=[{"question": "Buy the Primebook 2 Pro for ₹26,989?", "options": ["Yes", "No"]}],
    )

    result = await _click("Buy Now", pending.as_request(item, "yes, go ahead"))

    assert result.success


async def test_a_no_keeps_it_blocked(click_page):
    from voxos.core.conversation import pending

    item = pending.PendingQuestions(request="buy it", questions=[{"question": "Buy it?", "options": []}])

    result = await _click("Buy Now", pending.as_request(item, "no, just show me"))

    assert result.error_code == "NEEDS_CONFIRMATION"


# ─── Login requests and a flaky listener ────────────────────────────────────

def test_login_request_is_never_guessed_as_opening_an_app():
    from voxos.core.router import ExecutionClass, ExecutionRouter

    route = ExecutionRouter().route(
        "open_application", 0.9, {"app_name": "amazon"}, source="llm",
        request_text="log in to my amazon account",
    )
    assert route is ExecutionClass.REASONING


async def test_listener_survives_groq_json_failures():
    import httpx
    from openai import BadRequestError

    from voxos.agents.listener.agent import ListenerAgent, ListenerInput

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = BadRequestError(
        "json_validate_failed", response=httpx.Response(400, request=request),
        body={"error": {"code": "json_validate_failed", "failed_generation": ""}},
    )

    class Provider:
        calls = 0

        async def complete(self, messages, config):
            Provider.calls += 1
            raise error

    agent = ListenerAgent()
    agent._router = type("R", (), {"provider": Provider(), "config_for": lambda self, **k: None})()

    out = await agent._classify_intent(ListenerInput(text="which laptop brand do I like and what is my name?", session_id="s"))

    assert Provider.calls == 2
    assert out.requires_execution is True  # handed to the agents, not dropped


@pytest.mark.parametrize(("reply", "is_answer"), [
    ("yes", True),
    ("no, don't buy it", True),
    ("HP", True),                       # one of the options
    ("the second one", True),
    ("go with flipkart", True),
    ("which laptop brand do I like and what is my name?", False),
    ("open notepad", False),
    ("what time is it", False),
    ("I actually want you to check the reviews of all three laptops first please", False),
])
def test_new_requests_are_not_mistaken_for_answers(reply, is_answer):
    from voxos.core.conversation.pending import looks_like_answer

    questions = [{"question": "Which brand?", "options": ["HP", "Dell"]}]
    assert looks_like_answer(reply, questions) is is_answer


async def test_worker_stops_after_three_failures_in_a_row():
    llm = ScriptedLLM(worker=[
        {"action": "tool", "tool": "browser.click", "arguments": {"selector": "#gone"}},
        {"action": "tool", "tool": "browser.click", "arguments": {"selector": "#gone"}},
        {"action": "tool", "tool": "browser.click", "arguments": {"selector": "#gone"}},
        {"action": "finish", "answer": "I could not find the Add to Cart button."},
    ])

    result = await Worker(llm, FailingExecutor(), Overseer(), max_steps=6).run(
        SubGoal(id="g1", goal="add to cart"), request="", session_id="s",
    )

    assert len(result.steps) == 3
    assert "out of steps" in llm.prompts["worker"][-1].lower()


async def test_a_reply_ending_in_a_question_waits_for_the_answer():
    """ "Shall I proceed to checkout?" then "no" went to chat, which invented an action."""
    from voxos.core.conversation import pending

    session = str(uuid.uuid4())
    asking = _TaskManager({"reply": "The cheapest HP laptop is ₹48,090. It is in your cart. Shall I proceed to checkout?"})
    await _service(asking, _ActionListener()).handle_text("buy the cheapest HP laptop on amazon", session)

    waiting = pending.get(session)
    assert waiting is not None
    assert waiting.questions[0]["question"] == "Shall I proceed to checkout?"

    answering = _TaskManager({"reply": "Okay, I won't check out."})
    await _service(answering, _ChatListener()).handle_text("no, don't buy it", session)

    assert answering.listener_intents == ["answer_to_question"]


def test_chat_is_told_it_cannot_act():
    import inspect

    from voxos.core.conversation.service import ConversationService

    assert "cannot take any action" in inspect.getsource(ConversationService._llm_reply)


# ─── Closing windows needs the user's own request (live: "don't buy it" closed a window) ──

@pytest.mark.parametrize(("request_text", "allowed"), [
    ("no, don't buy it", False),
    ("search amazon for laptops", False),
    ("close the amazon window", True),
    ("quit notepad", True),
])
async def test_close_app_needs_the_user_to_ask(monkeypatch, request_text, allowed):
    import sys
    import types

    from voxos.core.request_context import user_request
    from voxos.tools.desktop.apps import CloseAppArgs, CloseAppTool

    monkeypatch.setitem(sys.modules, "pygetwindow", types.SimpleNamespace(getAllWindows=lambda: []))

    with user_request(request_text):
        result = await CloseAppTool()._execute(CloseAppArgs(window_title="Amazon"))

    if allowed:
        assert result.error_code == "NOT_FOUND"  # got past the gate; no such window in the test
    else:
        assert result.error_code == "NEEDS_CONFIRMATION"


def test_finish_without_an_answer_uses_its_conclusion():
    from voxos.orchestration.worker import answer_of

    assert answer_of({"action": "finish", "thought": "The cheapest HP laptop is the HP 15s at ₹32,990."}) \
        == "The cheapest HP laptop is the HP 15s at ₹32,990."
    assert answer_of({"action": "finish", "message": "Done."}) == "Done."
    assert answer_of({"action": "finish", "thought": "done"}) == ""
