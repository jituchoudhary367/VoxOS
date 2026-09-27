"""Regression coverage for deterministic browser intent routing."""


def test_open_youtube_routes_to_automation_without_llm() -> None:
    from voxos.core.router import ExecutionClass, ExecutionRouter

    route = ExecutionRouter().route("open_youtube", confidence=0.95, entities={})

    assert route is ExecutionClass.AUTOMATION


def test_open_browser_is_not_an_unknown_desktop_app() -> None:
    from voxos.agents.listener.agent import ListenerAgent

    result = ListenerAgent._fast_classify("open browser")

    assert result is not None
    assert result.intent == "open_browser"
    assert result.entities == {"url": "about:blank"}


def test_open_youtube_has_a_browser_tool_mapping() -> None:
    from voxos.core.router import ExecutionRouter

    assert ExecutionRouter().automation_tool("open_youtube") == "browser.open"


def test_explicit_google_search_and_follow_up_search_are_browser_actions() -> None:
    from voxos.agents.listener.agent import ListenerAgent

    google = ListenerAgent._fast_classify("open chrome and search google")
    follow_up = ListenerAgent._fast_classify("now search whatsapp")

    assert google is not None
    assert google.intent == "search_web_browser"
    assert google.entities == {"query": "google", "engine": "google"}
    assert follow_up is not None
    assert follow_up.intent == "search_web_browser"
    assert follow_up.entities == {"query": "whatsapp", "engine": "bing"}


# ─── Multi-part requests must reach the AI, not the keyword search ───────────
# Screenshot bug: "Search the web for today's AI news and also tell me my CPU
# and disk usage" was typed into Bing as one query.

import pytest


@pytest.mark.parametrize("text", [
    "Search the web for today's AI news and also tell me my CPU and disk usage",
    "search for python tutorials and then open notepad",
    "search youtube for lofi music also tell me the time",
    "now search whatsapp and check my battery",
])
def test_search_with_more_actions_goes_to_the_ai(text: str) -> None:
    from voxos.agents.listener.agent import ListenerAgent

    assert ListenerAgent._fast_classify(text) is None


@pytest.mark.parametrize(("text", "query", "engine"), [
    ("search google for best laptops", "best laptops", "google"),
    ("search youtube for lofi music", "lofi music", "youtube"),
    ("search the web for ai news", "ai news", "bing"),
    ("search for python tutorials", "python tutorials", "bing"),
    ("search wikipedia for alan turing", "alan turing", "wikipedia"),
    ("search best laptops on google", "best laptops", "google"),
    ("open chrome and search youtube for lofi music", "lofi music", "youtube"),
])
def test_search_query_names_the_engine_separately(text: str, query: str, engine: str) -> None:
    from voxos.agents.listener.agent import ListenerAgent

    result = ListenerAgent._fast_classify(text)

    assert result is not None
    assert result.intent == "search_web_browser"
    assert result.entities == {"query": query, "engine": engine}


def test_multi_part_request_is_orchestrated_after_the_ai_classifies_it() -> None:
    from voxos.core.router import ExecutionClass, ExecutionRouter
    from voxos.orchestration.graph import should_orchestrate

    text = "Search the web for today's AI news and also tell me my CPU and disk usage"
    # Whatever intent the LLM listener picks, the compound text keeps it off
    # the deterministic routes, and the orchestrator splits it.
    route = ExecutionRouter().route(
        "search_web_browser", 0.95, {"query": "AI news"}, source="llm", request_text=text,
    )
    assert route is ExecutionClass.REASONING
    assert should_orchestrate(text, "auto") is True
