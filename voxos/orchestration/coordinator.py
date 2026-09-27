"""
Coordinator — splits one request into sub-goals that Workers can run.

Independent sub-goals run in parallel; a sub-goal may depend only on
earlier ones, so the result is always a DAG. Each sub-goal names the tool
families its Worker needs, which keeps every Worker prompt small.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voxos.llm.router import TaskComplexity
from voxos.observability.logger import get_logger
from voxos.orchestration.llm import JsonAsker

log = get_logger(__name__)

TOOL_FAMILIES: dict[str, str] = {
    "system": "time, date, system info, CPU/RAM/disk/battery",
    "os": "files and folders: list, search, read, write, copy, move, rename, delete; shell commands",
    "document": "read PDF/Word/other documents",
    "desktop": "open/close apps, windows, keyboard, mouse, clipboard, screenshots",
    "browser": "drive a visible browser: open sites, search, click, type, YouTube",
    "web": "search the web and extract page text without a browser window",
    "memory": "save or search the user's remembered facts and preferences",
    "code": "run Python for calculations or data processing",
}


class SubGoal(BaseModel):
    id: str
    goal: str
    tools: list[str] = Field(default_factory=list)  # empty = every family
    depends_on: list[str] = Field(default_factory=list)


COORDINATOR_PROMPT = """You are the Coordinator of Vox, a voice assistant that controls the user's PC.
Split the user's request into the smallest set of sub-goals that separate agents can work on.

Respond with JSON: {{"goals": [{{"id": "g1", "goal": "...", "tools": ["family", ...], "depends_on": []}}]}}

Tool families:
{families}

Rules:
- Split only into parts that can run AT THE SAME TIME. Each agent can already do several steps in
  order and use its own results, so "find X, then do something with X" is ONE goal, not two.
- One sub-goal when the request is a single job or a single chain of steps. At most {max_goals} sub-goals.
- Keep each goal to what the user asked; do not add extra requirements ("top 3", "summarize each article").
- Say WHAT each goal must achieve, not how: no clicking, typing or search-bar steps (agents choose tools).
- Each goal is a complete, self-contained instruction (agents do not see the original request),
  with the concrete names, paths and language details from the request.
- depends_on: only when a goal truly needs another goal's result and they use unrelated tools; otherwise empty.
- tools: only the families that goal needs.
"""


def _families_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_FAMILIES.items())


async def decompose(
    request: str,
    *,
    memories: list[str],
    llm: JsonAsker,
    language: str = "en",
    max_goals: int = 4,
) -> list[SubGoal]:
    """Sub-goals for `request`; a single goal covering it if planning fails."""
    user = f"Request: {request}"
    if memories:
        user += "\nWhat you remember about the user:\n" + "\n".join(f"- {m}" for m in memories)
    try:
        data = await llm.ask(
            "coordinator",
            COORDINATOR_PROMPT.format(families=_families_text(), max_goals=max_goals),
            user,
            # Splitting a sentence is easy; the fast model does it in ~1 s
            # where the strong one took ~4 s before any worker could start.
            complexity=TaskComplexity.STANDARD,
        )
        goals = _clean(data.get("goals"), max_goals)
    except Exception as e:  # noqa: BLE001 — a failed split must not fail the request
        log.warning("coordinator_failed_single_goal", error=str(e))
        goals = []
    if not goals:
        goals = [SubGoal(id="g1", goal=request)]
    log.info("coordinator_goals", count=len(goals), goals=[g.goal[:80] for g in goals])
    return goals


def _clean(raw: Any, max_goals: int) -> list[SubGoal]:
    if not isinstance(raw, list):
        return []
    goals: list[SubGoal] = []
    for item in raw[:max_goals]:
        if not isinstance(item, dict) or not str(item.get("goal", "")).strip():
            continue
        goal_id = str(item.get("id") or f"g{len(goals) + 1}")
        if any(g.id == goal_id for g in goals):
            goal_id = f"g{len(goals) + 1}"
        earlier = {g.id for g in goals}  # only earlier goals: no cycles
        goals.append(SubGoal(
            id=goal_id,
            goal=str(item["goal"]).strip(),
            tools=[t for t in item.get("tools") or [] if t in TOOL_FAMILIES],
            depends_on=[d for d in item.get("depends_on") or [] if d in earlier],
        ))
    return goals
