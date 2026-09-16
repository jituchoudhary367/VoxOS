"""User-facing conversation models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AssistantReply(BaseModel):
    """A safe, human-readable response ready for presentation in the UI."""

    text: str
    kind: Literal["success", "error", "info"] = "info"
    action: str | None = None
