"""
Which browser tab the current work runs in.

All browser tools share one Playwright browser. Without tabs, parallel
workers and back-to-back commands navigated the same page and overwrote
each other's results. The tab key lives in a ContextVar, so every asyncio
task (each worker, each executor step) carries its own key without it being
passed through the tool interfaces.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_TAB_KEY: ContextVar[str | None] = ContextVar("vox_browser_tab", default=None)


def current_tab() -> str | None:
    return _TAB_KEY.get()


@contextmanager
def browser_tab(key: str) -> Iterator[None]:
    """Run browser tools inside the block in the tab named `key`."""
    token = _TAB_KEY.set(key)
    try:
        yield
    finally:
        _TAB_KEY.reset(token)


@contextmanager
def browser_tab_default(key: str) -> Iterator[None]:
    """Like browser_tab, unless an outer block (a worker) already chose a tab."""
    if _TAB_KEY.get() is not None:
        yield
        return
    with browser_tab(key):
        yield
