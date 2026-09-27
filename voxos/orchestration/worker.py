"""
Worker — works one sub-goal with an adaptive act → observe loop.

Instead of a plan written before anything runs, the Worker asks the LLM for
one action at a time and shows it the real result of the previous one, so
"find the biggest file, then open it" uses the file actually found.

Safety: every action goes through the Executor's `execute_step` (tool
lookup, permissions, argument validation, risk engine, approval gateway),
then the Overseer's `verify_step`. A blocked, denied or rejected action ends
the Worker at once — it never gets a turn to look for a way around it.

Self-reflection: when the Worker says it is finished, a critic checks the
answer against the goal and the observations. If something is missing the
Worker gets one more round (`extra_steps`) with the critique in view.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from voxos.config import get_settings
from voxos.core.permissions.manager import get_permission_manager
from voxos.core.task_manager.models import Task, TaskPlan, TaskStep
from voxos.llm.router import TaskComplexity
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.orchestration.coordinator import SubGoal
from voxos.orchestration.llm import JsonAsker
from voxos.tools.registry import get_tool_registry

log = get_logger(__name__)
metrics = get_metrics()

# The world said no. Retrying with other tools would be routing around it.
STOP_ERRORS = frozenset({"BLOCKED", "SECURITY_BLOCKED", "PERMISSION_DENIED", "APPROVAL_REJECTED"})
OBSERVATION_CHARS = 1500
# Tools that fetch a whole page; one successful read per worker is enough.
PAGE_READERS = frozenset({"web.extract", "browser.extract"})

SHELL_TOOL = "os.shell_command"
# Shell commands wait for the user's approval, so a worker only gets the
# shell tool when its goal is actually about running a command.
_ASKS_FOR_SHELL = re.compile(
    r"\b(?:command|shell|terminal|powershell|cmd|script|run|execute|install|pip|git|npm)\b",
    re.IGNORECASE,
)

# gpt-oss does not always use the key the prompt asks for.
_ANSWER_KEYS = ("answer", "final_answer", "summary", "response", "result", "output")


def answer_of(decision: dict[str, Any]) -> str:
    """The finish answer, under whichever key the model put it."""
    for key in _ANSWER_KEYS:
        value = decision.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class WorkerResult(BaseModel):
    goal_id: str
    goal: str
    status: Literal["done", "failed", "skipped"]
    answer: str = ""
    error: str = ""
    blocked: bool = False
    steps: list[dict[str, Any]] = Field(default_factory=list)
    reflections: int = 0


WORKER_PROMPT = """You are a Vox worker agent operating the user's Windows PC. Work toward ONE goal.
Each turn choose exactly one next action, see its real result, then choose again.

Respond with JSON, either
  {{"thought": "...", "action": "tool", "tool": "<tool name>", "arguments": {{...}}}}
or, when the goal is achieved or cannot be achieved,
  {{"thought": "...", "action": "finish", "answer": "<one or two plain sentences for the user>"}}

Tools, with their exact argument names:
{tools}

The user's workspace folder is {workspace} (use absolute paths; "my workspace" means this folder).
The user's home folder is {home}.

Rules:
- Never use os.shell_command when another tool can do the job: shell commands wait for the user's approval.
- To search inside a website, open its search results URL directly instead of typing into the page,
  e.g. https://www.amazon.in/s?k=asus+laptop, https://www.flipkart.com/search?q=asus+laptop,
  https://www.youtube.com/results?search_query=lofi+music. For a general web search use browser.search.
- A browser action is done once the page is open: finish then, saying what is on screen.
- For news and facts, answer from the search result snippets. Open at most ONE page, and only when
  the snippets do not contain the answer. Speed matters: the user is waiting.
- Do ONLY your goal. Other agents are handling the rest of the user's request at the same time; the full request is shown only for context.
- Use only the tools above, with exactly the argument names shown.
- Base facts on tool results, never guess (times, file names, sizes, contents).
- Use values from earlier results directly; do not repeat an action that already succeeded.
- If an action failed, fix the arguments or try another tool; after two failures, finish and say what went wrong.
- The answer is spoken aloud: short, no markdown, no raw JSON.
"""

REFLECT_PROMPT = """You check a Vox worker's result. Given the goal, what the worker did and its final
answer, decide whether the answer is achieved AND supported by the tool results.
Respond with JSON: {"satisfied": true|false, "missing": "<what is wrong or still to do, empty if satisfied>"}
Be strict about invented facts; be lenient about wording."""


class Worker:
    def __init__(
        self,
        llm: JsonAsker,
        executor: Any,
        overseer: Any,
        *,
        registry: Any | None = None,
        max_steps: int = 6,
        extra_steps: int = 3,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._overseer = overseer
        self._registry = registry or get_tool_registry()
        self._max_steps = max_steps
        self._extra_steps = extra_steps

    def tools_prompt(
        self, families: list[str], allow_shell: bool = True, permissions: Any = None
    ) -> str:
        from voxos.agents.strategist.agent import describe_tools

        lines = describe_tools(self._registry).splitlines()
        if not allow_shell:
            lines = [line for line in lines if not line.startswith(f"- {SHELL_TOOL}(")]
        # A tool the user may not use would only end the worker with
        # PERMISSION_DENIED; never offer it.
        manager = permissions or get_permission_manager()
        lines = [line for line in lines if manager.allows_tool(line[2:].split("(", 1)[0])]
        if not families:
            return "\n".join(lines)
        prefixes = tuple(f"- {family}." for family in families)
        return "\n".join(line for line in lines if line.startswith(prefixes))

    async def run(
        self,
        goal: SubGoal,
        *,
        request: str,
        session_id: str,
        context: list[str] | None = None,
        permissions: Any = None,
        language: str = "en",
    ) -> WorkerResult:
        system = WORKER_PROMPT.format(
            tools=self.tools_prompt(
                goal.tools,
                allow_shell=bool(_ASKS_FOR_SHELL.search(goal.goal)),
                permissions=permissions,
            ),
            workspace=get_settings().workspace.workspace_path,
            home=Path.home(),
        )
        task = Task(session_id=session_id, user_request=goal.goal, language=language)
        task.plan = TaskPlan(goal=goal.goal)
        observations: list[dict[str, Any]] = []
        critique = ""
        reflections = 0
        budget = self._max_steps

        while True:
            answer: str | None = None
            while len(observations) < budget:
                decision = await self._llm.ask(
                    "worker",
                    system,
                    self._turn_prompt(goal, request, context or [], observations, critique, language, session_id),
                    complexity=TaskComplexity.STANDARD,
                )
                decision = self._normalize(decision)
                if decision.get("action") == "finish" or not decision.get("tool"):
                    answer = answer_of(decision)
                    if not answer:
                        log.warning("worker_empty_finish", goal=goal.id, keys=sorted(decision))
                    break

                if decision["tool"] in PAGE_READERS and any(
                    o["tool"] in PAGE_READERS and o.get("status") == "SUCCESS" for o in observations
                ):
                    # Reading page after page made a news question take 25 s.
                    observation = {
                        "tool": decision["tool"],
                        "arguments": decision.get("arguments") or {},
                        "status": "SKIPPED",
                        "error": "Not run: you already read a page. Answer now from what you have.",
                        "error_code": "LIMIT",
                    }
                else:
                    observation = await self._act(
                        task, goal, len(observations) + 1, decision, session_id, permissions
                    )
                observations.append(observation)
                if observation.get("error_code") in STOP_ERRORS:
                    metrics.increment("orchestration.worker.stopped", reason=observation["error_code"])
                    return WorkerResult(
                        goal_id=goal.id, goal=goal.goal, status="failed",
                        error=observation.get("error") or observation["error_code"],
                        blocked=True, steps=observations, reflections=reflections,
                    )

            # An answer drawn from a tool result that succeeded and passed the
            # Overseer's check needs no critic; skipping it saves an LLM round
            # trip per worker. Answers with no evidence, or after a failed
            # last step, are still reviewed.
            last = observations[-1] if observations else None
            if answer and last and last.get("status") == "SUCCESS" and last.get("verified") is not False:
                metrics.increment("orchestration.worker.done")
                return WorkerResult(
                    goal_id=goal.id, goal=goal.goal, status="done", answer=answer,
                    steps=observations, reflections=reflections,
                )

            reflections += 1
            verdict = await self._reflect(goal, observations, answer)
            if verdict.get("satisfied") and answer:
                metrics.increment("orchestration.worker.done")
                return WorkerResult(
                    goal_id=goal.id, goal=goal.goal, status="done", answer=answer,
                    steps=observations, reflections=reflections,
                )
            if reflections > 1:
                if answer:
                    # The critic already had its retry. A real answer beats
                    # none; the synthesizer still sees what failed.
                    metrics.increment("orchestration.worker.done_unconfirmed")
                    return WorkerResult(
                        goal_id=goal.id, goal=goal.goal, status="done", answer=answer,
                        steps=observations, reflections=reflections,
                    )
                break
            critique = str(verdict.get("missing") or "The goal is not achieved yet.")
            budget = len(observations) + self._extra_steps
            log.info("worker_reflection_retry", goal=goal.id, missing=critique[:200])

        metrics.increment("orchestration.worker.failed")
        # The last tool error is the real cause; the critique only says the
        # goal was not met.
        failures = [o.get("error") for o in observations if o.get("error")]
        return WorkerResult(
            goal_id=goal.id, goal=goal.goal, status="failed",
            answer=answer or "",
            error=failures[-1] if failures else (critique or "Ran out of steps"),
            steps=observations, reflections=reflections,
        )

    def _normalize(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Accept the shapes gpt-oss uses besides the one asked for.

        {"action": "browser.open", "arguments": {...}} and {"name": ...} both
        mean a tool call; without this they read as "finished, no answer".
        """
        if decision.get("tool") or decision.get("action") == "finish":
            return decision
        for key in ("action", "name", "tool_name"):
            name = decision.get(key)
            if isinstance(name, str) and self._registry.get(name) is not None:
                return {**decision, "action": "tool", "tool": name}
        return decision

    async def _act(
        self,
        task: Task,
        goal: SubGoal,
        number: int,
        decision: dict[str, Any],
        session_id: str,
        permissions: Any,
    ) -> dict[str, Any]:
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        step = TaskStep(
            id=f"{goal.id}_s{number}",
            action=str(decision.get("thought") or decision["tool"])[:200],
            tool=str(decision["tool"]),
            arguments=dict(arguments),
        )
        task.plan.steps.append(step)
        result = await self._executor.execute_step(step, task, session_id, permissions, {})
        observation: dict[str, Any] = {
            "tool": step.tool,
            "arguments": arguments,
            "status": result.get("status"),
        }
        if result.get("status") == "SUCCESS":
            observation["data"] = result.get("data")
            verdict = await self._overseer.verify_step(result, step)
            observation["verified"] = bool(verdict.get("passed"))
            if not observation["verified"]:
                observation["verification"] = verdict.get("failure_reason") or "check failed"
        else:
            observation["error"] = result.get("error")
            observation["error_code"] = result.get("error_code")
        return observation

    async def _reflect(
        self, goal: SubGoal, observations: list[dict[str, Any]], answer: str | None
    ) -> dict[str, Any]:
        if not answer:
            return {
                "satisfied": False,
                "missing": 'You finished without an answer. Finish again with the findings in the "answer" field.',
            }
        user = (
            f"Goal: {goal.goal}\n\nWhat the worker did:\n{self._render(observations)}"
            f"\n\nFinal answer: {answer}"
        )
        try:
            return await self._llm.ask("reflect", REFLECT_PROMPT, user, complexity=TaskComplexity.SIMPLE)
        except Exception as e:  # noqa: BLE001 — a broken critic must not sink a good answer
            log.warning("worker_reflection_failed", error=str(e))
            return {"satisfied": True, "missing": ""}

    def _turn_prompt(
        self,
        goal: SubGoal,
        request: str,
        context: list[str],
        observations: list[dict[str, Any]],
        critique: str,
        language: str,
        session_id: str = "",
    ) -> str:
        parts = [f"User's request: {request}"] if request and request != goal.goal else []
        parts.append(f"Your goal: {goal.goal}")
        if session_id and (not goal.tools or "memory" in goal.tools):
            parts.append(f"For memory tools use user_id: {session_id}")
        parts.append(
            "Write the answer in English."
            if language in ("en", "auto", "")
            else f"Write the answer in the user's language ({language})."
        )
        if context:
            parts.append("Known context:\n" + "\n".join(f"- {c}" for c in context))
        parts.append("Actions so far:\n" + (self._render(observations) or "(none yet)"))
        if critique:
            parts.append(f"A reviewer rejected your previous answer: {critique}\nFix that before finishing.")
        parts.append("Choose the next action.")
        return "\n\n".join(parts)

    @staticmethod
    def _render(observations: list[dict[str, Any]]) -> str:
        lines = []
        for i, o in enumerate(observations, 1):
            args = json.dumps(o.get("arguments") or {}, ensure_ascii=False, default=str)
            if o.get("status") == "SUCCESS":
                data = json.dumps(o.get("data"), ensure_ascii=False, default=str)
                if len(data) > OBSERVATION_CHARS:
                    data = data[:OBSERVATION_CHARS] + "…(truncated)"
                line = f"{i}. {o['tool']}({args}) → OK: {data}"
                if o.get("verified") is False:
                    line += f"\n   but verification failed: {o.get('verification')}"
            else:
                line = f"{i}. {o['tool']}({args}) → FAILED [{o.get('error_code')}]: {o.get('error')}"
            lines.append(line)
        return "\n".join(lines)
