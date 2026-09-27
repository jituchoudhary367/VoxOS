"""
Learning about the user from conversation turns and finished tasks.

After a turn, one fast-model call pulls out durable facts (name,
preferences, routines, corrections, answers to Vox's questions) and facts
the user has retracted. It runs in the background, so it never delays a
reply, and it is skipped for short commands that carry no information.
Habits (tools, sites, hours) are counted from tasks without any LLM.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from voxos.core.profile.store import UserProfile, get_profile
from voxos.llm.router import TaskComplexity
from voxos.observability.logger import get_logger

log = get_logger(__name__)

LEARN_PROMPT = """You maintain what a personal voice assistant knows about its single user.
From the exchange below, extract only DURABLE facts about the user that will help later:
their name, preferences (brands, sites, music, apps, language), routines, work and projects,
budgets, and choices they made when the assistant asked them something.
Ignore one-off commands, the assistant's own words, and anything uncertain.
Write the user's name, when they give it, exactly as: "User's name is <Name>". Refer to the user as
"the user" in other facts (not by name).
Never record passwords, codes, card or account numbers.
If the user corrects or retracts something, list words identifying the old fact under "forget".

Known facts:
{known}

Respond with JSON: {{"facts": ["short third-person fact", ...], "forget": ["word or phrase", ...]}}
Return empty lists when there is nothing durable."""

_SECRETS = re.compile(r"\b(?:password|passcode|otp|pin|cvv|card number|account number)\b", re.IGNORECASE)
_FILLER = re.compile(r"^(?:hi|hello|hey|ok|okay|yes|no|thanks|thank you|pause|stop|resume)\b", re.IGNORECASE)


def worth_learning(user_text: str) -> bool:
    """Short commands ("pause", "what time is it") carry nothing to learn."""
    words = user_text.split()
    return len(words) >= 5 and not _FILLER.match(user_text.strip())


async def learn_from_exchange(
    llm: Any,
    profile: UserProfile,
    *,
    user_text: str,
    reply_text: str,
    vox_question: str | None = None,
) -> int:
    """Update `profile` from one turn. Returns the number of facts changed; never raises."""
    exchange = []
    if vox_question:
        exchange.append(f"Assistant had asked: {vox_question}")
    exchange += [f"User: {user_text}", f"Assistant: {reply_text}"]
    known = "\n".join(f"- {f['text']}" for f in profile.facts[-30:]) or "(none yet)"
    try:
        data = await llm.ask(
            "profile",
            LEARN_PROMPT.format(known=known),
            "\n".join(exchange),
            complexity=TaskComplexity.SIMPLE,
            max_tokens=300,
        )
    except Exception as e:  # noqa: BLE001 — learning is best effort
        log.warning("profile_learning_failed", error=str(e))
        return 0

    changed = 0
    for word in data.get("forget") or []:
        if isinstance(word, str):
            changed += profile.forget(word)
    facts = [f for f in data.get("facts") or [] if isinstance(f, str) and not _SECRETS.search(f)]
    changed += profile.add_facts(facts)
    if changed:
        log.info("profile_learned", facts=facts, changed=changed)
    return changed


def activity_from_task(task: Any) -> tuple[list[str], list[str]]:
    """(tools used, sites visited) by a finished task, in order."""
    steps: list[dict[str, Any]] = []
    result = getattr(task, "result", None)
    if isinstance(result, dict):
        for worker in result.get("workers") or []:
            steps += worker.get("steps") or []
    plan = getattr(task, "plan", None)
    if not steps and plan is not None:
        steps = [{"tool": s.tool, "arguments": s.arguments} for s in plan.steps if s.tool]
    tools = [s["tool"] for s in steps if s.get("tool")]
    sites = []
    for s in steps:
        url = (s.get("arguments") or {}).get("url")
        if isinstance(url, str) and "://" in url:
            host = urlparse(url).hostname or ""
            if host:
                sites.append(host.removeprefix("www."))
    return tools, sites


_background: set[asyncio.Task] = set()


def learn_in_background(
    *,
    user_text: str,
    reply_text: str,
    task: Any = None,
    vox_question: str | None = None,
    llm: Any = None,
) -> None:
    """Record habits now and learn facts in the background; never blocks a reply."""
    profile = get_profile()
    if task is not None:
        tools, sites = activity_from_task(task)
        if tools:
            profile.record_activity(tools=tools, sites=sites)
            profile.save()
    if not (worth_learning(user_text) or vox_question):
        return

    async def run() -> None:
        from voxos.orchestration.llm import RouterJsonLLM

        if await learn_from_exchange(
            llm or RouterJsonLLM(), profile,
            user_text=user_text, reply_text=reply_text, vox_question=vox_question,
        ):
            profile.save()

    job = asyncio.create_task(run())
    _background.add(job)
    job.add_done_callback(_background.discard)
