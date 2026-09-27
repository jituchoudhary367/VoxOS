"""
The user's own words for the request being worked on.

Set by the TaskManager around a run and read by tools that must check
what the USER asked, not what an agent decided (an agent's plan can be
steered by text on a web page; the user's request cannot).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_USER_REQUEST: ContextVar[str] = ContextVar("vox_user_request", default="")


def current_user_request() -> str:
    return _USER_REQUEST.get()


@contextmanager
def user_request(text: str) -> Iterator[None]:
    token = _USER_REQUEST.set(text or "")
    try:
        yield
    finally:
        _USER_REQUEST.reset(token)
