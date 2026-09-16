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
