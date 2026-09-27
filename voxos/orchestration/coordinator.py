"""
Coordinator — splits one request into sub-goals that Workers can run.

Independent sub-goals run in parallel; a sub-goal may depend only on
earlier ones, so the result is always a DAG. Each sub-goal names the tool
families its Worker needs, which keeps every Worker prompt small.

The Coordinator also resolves follow-ups ("pause it", "search more from
another company", "try again"): it sees the recent conversation and the
browser tabs that are open, and can send a goal to an existing tab instead
of opening a new one.
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
    "browser": "drive a visible browser: open sites, search, click, read the page, YouTube, "
               "pause/play/stop/skip video or music, log in with the user's saved accounts",
    "web": "search the web and extract page text without a browser window",
    "vision": "look at the current tab or the whole screen to see what is visible",
    "memory": "save or search the user's remembered facts and preferences",
    "code": "run Python for calculations or data processing",
}


class SubGoal(BaseModel):
    id: str
    goal: str
    tools: list[str] = Field(default_factory=list)  # empty = every family
    depends_on: list[str] = Field(default_factory=list)
    tab: str | None = None  # existing browser tab to work in; None = a new tab if needed


COORDINATOR_PROMPT = """You are the Coordinator of Vox, a voice assistant that controls the user's PC.
Work out what the user wants — using the conversation so far and what is on screen — and split it
into the smallest set of sub-goals that separate agents can work on.

Respond with JSON:
{{"goals": [{{"id": "g1", "goal": "...", "tools": ["family", ...], "depends_on": [], "tab": null}}]}}

Tool families:
{families}

Rules:
- Split only into parts that can run AT THE SAME TIME. Each agent can already do several steps in
  order and use its own results, so "find X, then do something with X" is ONE goal, not two.
- One sub-goal when the request is a single job or a single chain of steps. At most {max_goals} sub-goals.
- Keep each goal to what the user asked; do not add extra requirements ("top 3", "summarize each article").
- Keep buy / order / pay / send / delete requests as asked ("Buy the cheapest HP laptop on Amazon");
  do not water them down (e.g. to "add to cart"). The agent asks the user before the final step.
- Say WHAT each goal must achieve, not how: no clicking, typing or search-bar steps (agents choose tools).
- Each goal is a complete, self-contained instruction (agents do not see the original request),
  with the concrete names, paths and language details from the request.
- depends_on: only when a goal truly needs another goal's result and they use unrelated tools; otherwise empty.
- tools: only the families that goal needs.

Follow-ups — the request may refer to earlier turns or to what is on screen ("it", "that", "this",
"more", "another", "again", "stop", "pause", "try now", "go back"):
- Resolve the reference from the conversation and the open tabs and write the goal concretely
  ("Pause the video playing in the YouTube tab", "On the Amazon tab, search Amazon for HP laptops under 50000").
- If the goal continues work in an open tab, set "tab" to that tab's id so the agent works THERE.
  Open a new tab (tab null) only when the user asks for a new/another tab or the task is unrelated.
- "try now" / "try again" means: redo the last request that failed, as a concrete goal.
- When the user leaves a choice open ("another company", "some other laptop", "a good song"), make a
  sensible choice yourself (popular brands: HP, Lenovo, ASUS, Acer, Dell) and say which one in the goal.
- Only if you truly cannot tell what the user means at all, make one goal whose job is to answer
  asking the user to clarify (tools: []).
"""


def _families_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_FAMILIES.items())


def _history_text(history: list[dict[str, str]]) -> str:
    lines = []
    for turn in history[-8:]:
        who = "User" if turn.get("role") == "user" else "Vox"
        lines.append(f"{who}: {str(turn.get('content', ''))[:300]}")
    return "\n".join(lines)


async def decompose(
    request: str,
    *,
    memories: list[str],
    llm: JsonAsker,
    language: str = "en",
    max_goals: int = 4,
    screen: str = "",
    tab_ids: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
) -> list[SubGoal]:
    """Sub-goals for `request`; a single goal covering it if planning fails."""
    parts = []
    if history:
        parts.append("Conversation so far:\n" + _history_text(history))
    if screen:
        parts.append("Browser tabs open now:\n" + screen)
    if memories:
        parts.append("What you remember about the user:\n" + "\n".join(f"- {m}" for m in memories))
    parts.append(f"Request: {request}")
    try:
        data = await llm.ask(
            "coordinator",
            COORDINATOR_PROMPT.format(families=_families_text(), max_goals=max_goals),
            "\n\n".join(parts),
            # Splitting a sentence is easy; the fast model does it in ~1 s
            # where the strong one took ~4 s before any worker could start.
            complexity=TaskComplexity.STANDARD,
        )
        goals = _clean(data.get("goals"), max_goals, set(tab_ids or []))
    except Exception as e:  # noqa: BLE001 — a failed split must not fail the request
        log.warning("coordinator_failed_single_goal", error=str(e))
        goals = []
    if not goals:
        goals = [SubGoal(id="g1", goal=request)]
    log.info(
        "coordinator_goals",
        count=len(goals),
        goals=[g.goal[:80] for g in goals],
        tabs=[g.tab for g in goals],
    )
    return goals


def _clean(raw: Any, max_goals: int, tab_ids: set[str] | None = None) -> list[SubGoal]:
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
        tab = item.get("tab")
        # Two parallel goals must never drive the same tab.
        taken = {g.tab for g in goals}
        goals.append(SubGoal(
            id=goal_id,
            goal=str(item["goal"]).strip(),
            tools=[t for t in item.get("tools") or [] if t in TOOL_FAMILIES],
            depends_on=[d for d in item.get("depends_on") or [] if d in earlier],
            tab=tab if isinstance(tab, str) and tab in (tab_ids or set()) and tab not in taken else None,
        ))
    return goals
