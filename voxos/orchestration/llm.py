"""
JSON-speaking LLM access for the orchestration agents.

Every agent asks for a JSON object under a role name ("coordinator",
"worker", "reflect", "synthesizer"); the role only labels logs and metrics.
Calls go through the LLMRouter, so they share the key pool and fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from openai import BadRequestError

from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class JsonAsker(Protocol):
    async def ask(self, role: str, system: str, user: str, **kwargs: Any) -> dict[str, Any]: ...


def parse_json_object(text: str) -> dict[str, Any]:
    """The first JSON object in `text`, tolerating code fences and chatter."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        try:
            value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{", start + 1)
    raise ValueError("LLM reply contained no JSON object")


def recover_failed_generation(error: Exception) -> dict[str, Any] | None:
    """The JSON object inside a Groq `tool_use_failed` rejection, if any.

    In JSON mode gpt-oss sometimes wraps its answer in a tool call; Groq then
    rejects the request but returns the generation, whose arguments are the
    object that was asked for.
    """
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    detail = body.get("error") if isinstance(body.get("error"), dict) else body
    if detail.get("code") != "tool_use_failed" or not detail.get("failed_generation"):
        return None
    try:
        value = parse_json_object(str(detail["failed_generation"]))
    except ValueError:
        return None
    arguments = value.get("arguments")
    name = str(value.get("name") or "")
    if name and isinstance(arguments, dict):
        # A native call to one of our tools (e.g. "web.extract"): the model
        # meant a worker action, not an answer object.
        if "." in name and "action" not in arguments:
            return {"action": "tool", "tool": name, "arguments": arguments}
        return arguments
    return value


def _is_json_generation_error(error: Exception) -> bool:
    body = getattr(error, "body", None)
    detail = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
    return isinstance(detail, dict) and detail.get("code") in {"json_validate_failed", "tool_use_failed"}


class RouterJsonLLM:
    """JsonAsker backed by the app's LLMRouter."""

    def __init__(self, router: Any | None = None) -> None:
        self._router = router or get_llm_router()

    async def ask(
        self,
        role: str,
        system: str,
        user: str,
        *,
        complexity: TaskComplexity = TaskComplexity.STANDARD,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        config = self._router.config_for(
            complexity,
            temperature=0.1,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            system_prompt=system,
        )
        messages = [LLMMessage(role="user", content=user)]
        for attempt in range(2):
            try:
                async with metrics.async_timer("orchestration.llm", role=role):
                    response = await self._router.provider.complete(messages, config)
                break
            except BadRequestError as e:
                recovered = recover_failed_generation(e)
                if recovered is not None:
                    metrics.increment("orchestration.llm.recovered_tool_call", role=role)
                    return recovered
                # Groq sometimes fails its own JSON check with an empty
                # generation; the same request usually succeeds next time.
                if attempt == 0 and _is_json_generation_error(e):
                    log.warning("orchestration_llm_json_retry", role=role)
                    continue
                raise
        metrics.increment("orchestration.llm.tokens", value=response.total_tokens, role=role)
        return parse_json_object(response.content)
