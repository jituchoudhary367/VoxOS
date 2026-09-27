"""Vox knows what it did and what is on screen, and acts on follow-ups there.

Live session: "pause" and "stop the music" failed, "play that" tried to open
an app with no name, and a follow-up search would have opened a new tab.
"""

from __future__ import annotations

from typing import Any

import pytest

from voxos.orchestration.coordinator import SubGoal, _clean, decompose
from voxos.orchestration.graph import VoxOrchestrator
from voxos.orchestration.worker import Worker
from voxos.tools.browser import media as media_module
from voxos.tools.browser import tools as browser
from voxos.tools.browser.media import BrowserMediaArgs, BrowserMediaTool, choose_tab
from voxos.tools.browser.tabs import current_tab


# ─── Fakes ──────────────────────────────────────────────────────────────────

class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    def __init__(self, url: str, title: str, media: str = "none") -> None:
        self.url, self._title, self.media = url, title, media
        self.closed = False
        self.keyboard = FakeKeyboard()
        self.scripts: list[str] = []

    def is_closed(self) -> bool:
        return self.closed

    async def title(self) -> str:
        return self._title

    async def evaluate(self, script: str) -> Any:
        if script == browser._MEDIA_STATE_JS:
            return self.media
        self.scripts.append(script)
        if "pause()" in script and "paused" not in script:
            self.media = "paused"
        elif ".play()" in script and self.media == "paused":
            self.media = "playing"
        return None


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages


@pytest.fixture
def two_tabs(monkeypatch):
    youtube = FakePage("https://www.youtube.com/watch?v=x", "Choosin' Texas - YouTube", media="playing")
    amazon = FakePage("https://www.amazon.in/s?k=msi+laptop", "Amazon.in : MSI laptop")
    monkeypatch.setattr(browser, "_context_instance", FakeContext([youtube, amazon]))
    monkeypatch.setattr(browser, "_tabs", {"yt": youtube, "amz": amazon})
    monkeypatch.setattr(browser, "_tab_labels", {"yt": "Play trending music", "amz": "Search Amazon for MSI laptops"})
    monkeypatch.setattr(browser, "_last_page", amazon)
    return youtube, amazon


# ─── What is on screen ──────────────────────────────────────────────────────

async def test_tab_state_lists_titles_media_and_purpose(two_tabs):
    state = await browser.browser_tabs_state()

    by_tab = {t["tab"]: t for t in state}
    assert by_tab["yt"]["media"] == "playing"
    assert by_tab["amz"]["active"] is True
    text = browser.describe_tabs(state)
    assert 'tab "yt"' in text and "media playing" in text
    assert "Search Amazon for MSI laptops" in text


async def test_tabs_the_user_opened_by_hand_get_ids(monkeypatch):
    own = FakePage("https://news.example", "News")
    monkeypatch.setattr(browser, "_context_instance", FakeContext([own]))
    monkeypatch.setattr(browser, "_tabs", {})
    monkeypatch.setattr(browser, "_tab_labels", {})
    monkeypatch.setattr(browser, "_last_page", None)

    state = await browser.browser_tabs_state()

    assert state[0]["tab"] == "tab-1"
    assert browser.page_for_tab("tab-1") is own


# ─── browser.media ──────────────────────────────────────────────────────────

async def test_pause_finds_the_playing_tab_even_when_another_tab_is_active(two_tabs):
    youtube, amazon = two_tabs

    result = await BrowserMediaTool()._execute(BrowserMediaArgs(action="pause"))

    assert result.success
    assert result.data["tab"] == "yt"
    assert youtube.media == "paused"
    assert amazon.scripts == []


async def test_resume_finds_the_paused_tab(two_tabs):
    youtube, _ = two_tabs
    youtube.media = "paused"

    result = await BrowserMediaTool()._execute(BrowserMediaArgs(action="play"))

    assert result.data["tab"] == "yt"
    assert youtube.media == "playing"


async def test_next_on_youtube_uses_its_shortcut(two_tabs):
    youtube, _ = two_tabs

    await BrowserMediaTool()._execute(BrowserMediaArgs(action="next"))

    assert youtube.keyboard.pressed == ["Shift+N"]


async def test_without_browser_media_pause_sends_stop_not_play_pause(monkeypatch):
    """Play/Pause would start music that was not playing."""
    import sys
    import types

    pressed: list[str] = []
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(press=pressed.append))
    monkeypatch.setattr(browser, "_context_instance", None)

    result = await BrowserMediaTool()._execute(BrowserMediaArgs(action="pause"))

    assert result.success
    assert pressed == ["stop"]
    assert result.data["tab"] is None


def test_choose_tab_prefers_the_active_playing_tab():
    state = [
        {"tab": "a", "media": "playing", "active": False},
        {"tab": "b", "media": "playing", "active": True},
        {"tab": "c", "media": "paused", "active": False},
    ]
    assert choose_tab(state, "pause")["tab"] == "b"
    assert choose_tab(state, "play")["tab"] == "c"
    assert choose_tab([{"tab": "a", "media": "none", "active": True}], "pause") is None


# ─── Coordinator resolves follow-ups to existing tabs ───────────────────────

class ScriptedLLM:
    def __init__(self, **scripts: list[Any]) -> None:
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.prompts: dict[str, list[str]] = {}

    async def ask(self, role: str, system: str, user: str, **_: Any) -> dict[str, Any]:
        self.prompts.setdefault(role, []).append(user)
        return self.scripts[role].pop(0)


def test_coordinator_only_keeps_real_and_unshared_tabs():
    goals = _clean([
        {"id": "g1", "goal": "search HP", "tools": ["browser"], "tab": "amz"},
        {"id": "g2", "goal": "search Dell", "tools": ["browser"], "tab": "amz"},  # same tab: not parallel-safe
        {"id": "g3", "goal": "x", "tools": [], "tab": "made-up"},
    ], 4, {"amz", "yt"})

    assert [g.tab for g in goals] == ["amz", None, None]


async def test_coordinator_sees_conversation_and_tabs():
    llm = ScriptedLLM(coordinator=[{"goals": [
        {"id": "g1", "goal": "On the Amazon tab, search Amazon for HP laptops under 50000", "tools": ["browser"], "tab": "amz"},
    ]}])

    goals = await decompose(
        "search more laptops from another company",
        memories=[], llm=llm,
        screen='- tab "amz": Amazon.in : MSI laptop <https://www.amazon.in/s?k=msi+laptop>',
        tab_ids=["amz"],
        history=[
            {"role": "user", "content": "search amazon for msi laptop under 50k"},
            {"role": "assistant", "content": "Amazon shows MSI laptops under 50,000."},
        ],
    )

    prompt = llm.prompts["coordinator"][0]
    assert "msi laptop under 50k" in prompt and 'tab "amz"' in prompt
    assert goals[0].tab == "amz"


# ─── Worker works in the tab it was given ───────────────────────────────────

class TabRecordingExecutor:
    def __init__(self) -> None:
        self.tabs: list[str | None] = []

    async def execute_step(self, step, task, session_id, permissions, prior_results=None):
        self.tabs.append(current_tab())
        return {"step_id": step.id, "status": "SUCCESS", "data": {"ok": True}}


class PassingOverseer:
    async def verify_step(self, step_result, plan_step):
        return {"passed": True}


async def test_worker_uses_the_assigned_tab():
    executor = TabRecordingExecutor()
    llm = ScriptedLLM(worker=[
        {"action": "tool", "tool": "browser.open", "arguments": {"url": "https://www.amazon.in/s?k=hp+laptop"}},
        {"action": "finish", "answer": "Amazon now shows HP laptops."},
    ])

    await Worker(llm, executor, PassingOverseer()).run(
        SubGoal(id="g1", goal="search Amazon for HP laptops", tools=["browser"], tab="amz"),
        request="", session_id="s",
    )

    assert executor.tabs == ["amz"]


async def test_worker_without_a_tab_gets_its_own():
    executor = TabRecordingExecutor()
    llm = ScriptedLLM(worker=[
        {"action": "tool", "tool": "browser.open", "arguments": {"url": "https://example.com"}},
        {"action": "finish", "answer": "Opened."},
    ])

    await Worker(llm, executor, PassingOverseer()).run(
        SubGoal(id="g1", goal="open example", tools=["browser"]), request="", session_id="s",
    )

    assert executor.tabs[0] is not None and executor.tabs[0].startswith("g1-")


# ─── The graph hands the Coordinator what Vox knows ─────────────────────────

async def test_graph_passes_screen_and_history_without_repeating_the_request():
    llm = ScriptedLLM(
        coordinator=[{"goals": [{"id": "g1", "goal": "Pause the video in the YouTube tab", "tools": ["browser"], "tab": "yt"}]}],
        worker=[
            {"action": "tool", "tool": "browser.media", "arguments": {"action": "pause"}},
            {"action": "finish", "answer": "Paused."},
        ],
    )

    async def observe():
        return '- tab "yt": Choosin\' Texas <https://youtube.com> (media playing)', ["yt"]

    def history(_session: str):
        return [
            {"role": "user", "content": "play the best trending song"},
            {"role": "assistant", "content": "Playing Choosin' Texas."},
            {"role": "user", "content": "stop that one"},
        ]

    executor = TabRecordingExecutor()
    orchestrator = VoxOrchestrator(llm=llm, executor=executor, overseer=PassingOverseer(), observe=observe, history=history)
    outcome = await orchestrator.run("stop that one", session_id="s")

    prompt = llm.prompts["coordinator"][0]
    assert "Choosin' Texas" in prompt and "media playing" in prompt
    assert prompt.count("stop that one") == 1  # history's last turn is the request itself
    assert executor.tabs == ["yt"]
    assert outcome.reply == "Paused."


async def test_graph_still_plans_when_observing_the_screen_fails():
    llm = ScriptedLLM(
        coordinator=[{"goals": [{"id": "g1", "goal": "x", "tools": []}]}],
        worker=[{"action": "finish", "answer": "ok"}],
        reflect=[{"satisfied": True, "missing": ""}],
    )

    async def broken():
        raise RuntimeError("browser crashed")

    outcome = await VoxOrchestrator(
        llm=llm, executor=TabRecordingExecutor(), overseer=PassingOverseer(), observe=broken,
    ).run("x", session_id="s")

    assert outcome.reply == "ok"


# ─── What Vox says afterwards ───────────────────────────────────────────────

@pytest.mark.parametrize(("data", "expected"), [
    ({"action": "pause", "tab": "yt", "title": "Choosin' Texas", "media": "paused"}, "Paused Choosin' Texas."),
    ({"action": "play", "tab": "yt", "title": "Lofi", "media": "playing"}, "Playing Lofi."),
    ({"action": "pause", "tab": None, "title": "system media keys", "media": "unknown"},
     "Sent the pause command to your media player."),
])
def test_media_results_are_spoken_plainly(data, expected):
    from voxos.core.conversation.service import ConversationService

    assert ConversationService._sentence_for(data)[0] == expected


# ─── Second live run: wrong media tab, stray tabs, screen questions ─────────

def test_resume_prefers_what_vox_paused_over_a_random_product_video():
    state = [
        {"tab": "amz", "url": "https://www.amazon.in/s?k=msi", "media": "paused", "active": True},
        {"tab": "yt", "url": "https://www.youtube.com/watch?v=x", "media": "paused", "active": False},
    ]
    assert choose_tab(state, "play")["tab"] == "yt"  # a media site beats a shop's promo video
    assert choose_tab(state, "play", last_media_tab="amz")["tab"] == "amz"


def test_resume_ignores_non_media_sites_nobody_paused():
    state = [{"tab": "amz", "url": "https://www.amazon.in/s?k=msi", "media": "paused", "active": True}]

    assert choose_tab(state, "play") is None  # falls back to the system media key


def test_pause_prefers_a_media_site_that_is_playing():
    state = [
        {"tab": "amz", "url": "https://www.amazon.in/", "media": "playing", "active": True},
        {"tab": "sp", "url": "https://open.spotify.com/track/1", "media": "playing", "active": False},
    ]
    assert choose_tab(state, "pause")["tab"] == "sp"


async def test_new_tab_inside_a_task_uses_the_tasks_tab(monkeypatch):
    from voxos.tools.browser.tabs import browser_tab
    from voxos.tools.browser.tools import BrowserNewTabArgs, BrowserNewTabTool

    class Page(FakePage):
        async def goto(self, url, **_):
            self.url = url

    start = Page("about:blank", "")

    class Ctx:
        pages = [start]

        async def new_page(self):
            raise AssertionError("no stray tab")

    class Browser:
        def is_connected(self):
            return True

    monkeypatch.setattr(browser, "_browser_instance", Browser())
    monkeypatch.setattr(browser, "_context_instance", Ctx())
    monkeypatch.setattr(browser, "_page_instance", start)
    monkeypatch.setattr(browser, "_tabs", {})
    monkeypatch.setattr(browser, "_last_page", None)

    with browser_tab("g1-abc"):
        result = await BrowserNewTabTool()._execute(BrowserNewTabArgs(url="https://www.youtube.com"))

    assert result.success
    assert browser._tabs["g1-abc"] is start
    assert browser._page_instance is start  # the start page is not replaced


@pytest.mark.parametrize("text", [
    "what is shown on the amazon page right now",
    "what's on my screen",
    "which video is playing in the tab",
    "read what is visible in the window",
])
def test_screen_questions_need_context(text):
    from voxos.core.router import is_follow_up

    assert is_follow_up(text)


def test_page_text_gets_a_bigger_observation_budget():
    from voxos.orchestration.worker import Worker

    long_text = "Acer Aspire 7 ₹49,990 " * 300
    rendered = Worker._render([{
        "tool": "browser.extract", "arguments": {}, "status": "SUCCESS", "data": {"text": long_text},
    }])
    assert len(rendered) > 3500


async def test_navigation_that_turns_into_a_download_is_retried_once(monkeypatch):
    monkeypatch.setattr(browser.asyncio, "sleep", lambda _s: _noop())
    calls = []

    class Page:
        async def goto(self, url, **_):
            calls.append(url)
            if len(calls) == 1:
                raise Exception("Page.goto: Download is starting")

    await browser.goto(Page(), "https://www.amazon.in/s?k=hp")

    assert calls == ["https://www.amazon.in/s?k=hp"] * 2


async def test_navigation_that_keeps_downloading_says_so(monkeypatch):
    monkeypatch.setattr(browser.asyncio, "sleep", lambda _s: _noop())

    class Page:
        async def goto(self, url, **_):
            raise Exception("Page.goto: Download is starting")

    with pytest.raises(RuntimeError, match="download"):
        await browser.goto(Page(), "https://www.amazon.in/s?k=hp")


async def _noop():
    return None


async def test_vision_looks_at_the_workers_tab_not_the_last_used_one(two_tabs, monkeypatch):
    from voxos.tools.browser.tabs import browser_tab
    from voxos.tools.vision import tools as vision

    youtube, amazon = two_tabs
    shot = []

    async def screenshot(page):
        shot.append(page)
        return b"png"

    youtube.screenshot = lambda **_: screenshot(youtube)
    amazon.screenshot = lambda **_: screenshot(amazon)
    monkeypatch.setattr(vision, "_to_jpeg_data_url", lambda _b: "data:image/jpeg;base64,x")

    with browser_tab("yt"):
        _, source = await vision.capture("browser")

    assert shot == [youtube]
    assert "youtube" in source
