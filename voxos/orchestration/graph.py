"""
VoxOrchestrator — the multi-agent run as a LangGraph state graph.

    recall ─▶ coordinate ─▶ dispatch ─┬─▶ worker (× ready goals, in parallel) ─▶ dispatch
                                      └─▶ synthesize ─▶ END

`dispatch` sends every sub-goal whose dependencies are done to its own
`worker` branch in the same superstep (LangGraph `Send`), so independent
goals run concurrently; results are collected by an append reducer. A goal
whose dependency failed is recorded as skipped instead of being run.

LangGraph only orchestrates. Tools run through the Executor (risk gate,
permissions, approvals) and LLM calls through the LLMRouter (key pool).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Awaitable, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from voxos.core.router import is_compound_request
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.orchestration.coordinator import SubGoal, decompose
from voxos.orchestration.llm import JsonAsker
from voxos.orchestration.synthesizer import synthesize
from voxos.orchestration.worker import Worker, WorkerResult

log = get_logger(__name__)
metrics = get_metrics()

Recall = Callable[[str], Awaitable[list[str]]]


def should_orchestrate(text: str, mode: str) -> bool:
    """Whether a REASONING request goes to the multi-agent graph.

    auto: only compound requests (the single-plan Strategist handles the
    rest, as benchmarked); graph: every reasoning request; pipeline: never.
    """
    if mode == "graph":
        return True
    if mode == "auto":
        return is_compound_request(text)
    return False


class _State(TypedDict, total=False):
    request: str
    session_id: str
    language: str
    memories: list[str]
    goals: list[dict[str, Any]]
    results: Annotated[list[dict[str, Any]], operator.add]
    reply: str


@dataclass
class OrchestrationOutcome:
    reply: str
    goals: list[SubGoal]
    results: list[WorkerResult] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return any(r.status == "done" for r in self.results)

    @property
    def blocked(self) -> bool:
        return any(r.blocked for r in self.results)


async def _no_memories(session_id: str) -> list[str]:
    return []


class VoxOrchestrator:
    def __init__(
        self,
        *,
        llm: JsonAsker,
        executor: Any,
        overseer: Any,
        recall: Recall | None = None,
        max_goals: int = 4,
        worker_steps: int = 6,
    ) -> None:
        self._llm = llm
        self._recall = recall or _no_memories
        self._max_goals = max_goals
        self._worker = Worker(llm, executor, overseer, max_steps=worker_steps)
        self._graph = self._build()

    def _build(self) -> Any:
        graph = StateGraph(_State)
        graph.add_node("recall", self._recall_node)
        graph.add_node("coordinate", self._coordinate_node)
        graph.add_node("dispatch", self._dispatch_node)
        graph.add_node("worker", self._worker_node)
        graph.add_node("synthesize", self._synthesize_node)
        graph.add_edge(START, "recall")
        graph.add_edge("recall", "coordinate")
        graph.add_edge("coordinate", "dispatch")
        graph.add_conditional_edges("dispatch", self._route_ready, ["worker", "synthesize"])
        graph.add_edge("worker", "dispatch")
        graph.add_edge("synthesize", END)
        return graph.compile()

    async def run(
        self,
        request: str,
        *,
        session_id: str,
        language: str = "en",
        permissions: Any = None,
    ) -> OrchestrationOutcome:
        metrics.increment("orchestration.runs")
        final = await self._graph.ainvoke(
            {"request": request, "session_id": session_id, "language": language, "results": []},
            config={"configurable": {"permissions": permissions}, "recursion_limit": 50},
        )
        goals = [SubGoal(**g) for g in final.get("goals", [])]
        order = {g.id: i for i, g in enumerate(goals)}
        results = sorted(
            (WorkerResult(**r) for r in final.get("results", [])),
            key=lambda r: order.get(r.goal_id, len(order)),
        )
        return OrchestrationOutcome(reply=final.get("reply", ""), goals=goals, results=results)

    # ── nodes ────────────────────────────────────────────────────────────────

    async def _recall_node(self, state: _State) -> dict[str, Any]:
        try:
            memories = await self._recall(state["session_id"])
        except Exception as e:  # noqa: BLE001 — memory is a nice-to-have
            log.warning("orchestration_recall_failed", error=str(e))
            memories = []
        return {"memories": memories}

    async def _coordinate_node(self, state: _State) -> dict[str, Any]:
        goals = await decompose(
            state["request"],
            memories=state.get("memories", []),
            llm=self._llm,
            language=state.get("language", "en"),
            max_goals=self._max_goals,
        )
        return {"goals": [g.model_dump() for g in goals]}

    async def _dispatch_node(self, state: _State) -> dict[str, Any]:
        by_id = {r["goal_id"]: r for r in state.get("results", [])}
        skipped = []
        for goal in state.get("goals", []):
            if goal["id"] in by_id:
                continue
            if any(by_id.get(d, {}).get("status") in {"failed", "skipped"} for d in goal["depends_on"]):
                result = WorkerResult(
                    goal_id=goal["id"], goal=goal["goal"], status="skipped",
                    error="An earlier part it depends on failed.",
                )
                skipped.append(result.model_dump())
                by_id[goal["id"]] = skipped[-1]
        return {"results": skipped} if skipped else {}

    def _route_ready(self, state: _State) -> list[Send] | str:
        by_id = {r["goal_id"]: r for r in state.get("results", [])}
        ready = [
            goal for goal in state.get("goals", [])
            if goal["id"] not in by_id and all(d in by_id for d in goal["depends_on"])
        ]
        if not ready:
            return "synthesize"
        return [
            Send("worker", {
                "goal": goal,
                "context": list(state.get("memories", [])) + [
                    f"Result of '{by_id[d]['goal']}': {by_id[d]['answer']}" for d in goal["depends_on"]
                ],
                # With several goals each worker sees only its own, or it
                # starts doing the other workers' parts too.
                "request": state["request"] if len(state.get("goals", [])) == 1 else "",
                "session_id": state["session_id"],
                "language": state.get("language", "en"),
            })
            for goal in ready
        ]

    async def _worker_node(self, payload: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        goal = SubGoal(**payload["goal"])
        try:
            result = await self._worker.run(
                goal,
                request=payload["request"],
                session_id=payload["session_id"],
                context=payload["context"],
                permissions=(config.get("configurable") or {}).get("permissions"),
                language=payload["language"],
            )
        except Exception as e:  # noqa: BLE001 — one broken worker must not sink the others
            log.error("worker_crashed", goal=goal.id, error=str(e), exc_info=True)
            result = WorkerResult(goal_id=goal.id, goal=goal.goal, status="failed", error="Internal error")
        log.info(
            "worker_finished",
            goal=goal.id,
            status=result.status,
            steps=[f"{o.get('tool')}:{o.get('status')}" for o in result.steps],
            answer=result.answer[:300],
            error=result.error[:200],
        )
        return {"results": [result.model_dump()]}

    async def _synthesize_node(self, state: _State) -> dict[str, Any]:
        results = [WorkerResult(**r) for r in state.get("results", [])]
        reply = await synthesize(state["request"], results, self._llm, state.get("language", "en"))
        return {"reply": reply}
