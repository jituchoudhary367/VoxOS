"""Each worker / command gets its own browser tab.

With one shared page, parallel workers (and back-to-back commands)
navigated the same tab and overwrote each other's results.
"""

from __future__ import annotations

import asyncio

import pytest

from voxos.tools.browser import tools as browser
from voxos.tools.browser.tabs import browser_tab, browser_tab_default


class FakePage:
    def __init__(self, n: int) -> None:
        self.n = n
        self.url = "about:blank"
        self.closed = False
        self.fronted = 0

    def is_closed(self) -> bool:
        return self.closed

    async def bring_to_front(self) -> None:
        self.fronted += 1


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage(len(self.pages))
        self.pages.append(page)
        return page


class FakeBrowser:
    def is_connected(self) -> bool:
        return True


@pytest.fixture
def fake_session(monkeypatch):
    context = FakeContext()
    main = FakePage(-1)
    monkeypatch.setattr(browser, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(browser, "_browser_instance", FakeBrowser())
    monkeypatch.setattr(browser, "_context_instance", context)
    monkeypatch.setattr(browser, "_page_instance", main)
    monkeypatch.setattr(browser, "_tabs", {})
    monkeypatch.setattr(browser, "_last_page", None)
    return context, main


async def test_parallel_workers_navigate_in_separate_tabs(fake_session):
    context, main = fake_session

    async def worker(key: str):
        with browser_tab(key):
            first = await browser._get_browser_session(navigate=True)
            await asyncio.sleep(0)
            again = await browser._get_browser_session()
            assert again is first
            return first

    a, b = await asyncio.gather(worker("task1:g1"), worker("task1:g2"))

    assert a is not b


async def test_first_tab_reuses_the_untouched_start_page(fake_session):
    context, main = fake_session

    with browser_tab("task1"):
        page = await browser._get_browser_session(navigate=True)

    assert page is main
    assert context.pages == []


async def test_a_new_command_opens_a_new_tab_and_keeps_the_old_result(fake_session):
    context, main = fake_session

    with browser_tab("task1"):
        first = await browser._get_browser_session(navigate=True)
    first.url = "https://www.bing.com/search?q=laptops"
    with browser_tab("task2"):
        second = await browser._get_browser_session(navigate=True)

    assert second is not first
    assert first.url == "https://www.bing.com/search?q=laptops"


async def test_follow_up_without_navigation_uses_the_latest_tab(fake_session):
    context, main = fake_session

    with browser_tab("task1"):
        searched = await browser._get_browser_session(navigate=True)
    searched.url = "https://www.youtube.com/results?search_query=lofi"
    with browser_tab("task2"):  # "now scroll down"
        scrolled = await browser._get_browser_session()

    assert scrolled is searched
    assert context.pages == []


async def test_default_key_does_not_override_a_worker_key(fake_session):
    with browser_tab("worker-key"):
        with browser_tab_default("task-key"):
            from voxos.tools.browser.tabs import current_tab

            assert current_tab() == "worker-key"
    with browser_tab_default("task-key"):
        from voxos.tools.browser.tabs import current_tab

        assert current_tab() == "task-key"
