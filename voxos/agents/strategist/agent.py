"""
Strategist Agent — "How should we accomplish it?"

Responsibilities:
- Decompose user intent into a step-by-step plan
- Select the right tool for each step
- Identify dependencies between steps
- Estimate risk level
- Support replanning on failure
- NEVER execute tools directly
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from voxos.agents.base import BaseAgent
from voxos.core.events.bus import JarvisEvent, get_event_bus
from voxos.core.events.types import EventType
from voxos.core.task_manager.models import TaskPlan, TaskStep
from voxos.llm.base import LLMMessage
from voxos.llm.router import TaskComplexity, get_llm_router
from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics
from voxos.tools.registry import get_tool_registry

log = get_logger(__name__)
metrics = get_metrics()


# ─────────────────────────────────────────────────────────────────────────────
# I/O schemas
# ─────────────────────────────────────────────────────────────────────────────

class StrategistInput(BaseModel):
    intent: str
    entities: dict[str, Any] = Field(default_factory=dict)
    user_request: str
    session_id: str | None = None
    task_id: str | None = None
    language: str = "en"
    previous_failures: list[str] = Field(default_factory=list)


class StrategistOutput(BaseModel):
    plan: TaskPlan
    reasoning: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Intent → tool mapping (deterministic fast-path)
# ─────────────────────────────────────────────────────────────────────────────

INTENT_TO_TOOL: dict[str, str] = {
    "get_current_time": "system.get_time",
    "get_system_info": "system.get_info",
    "get_resources": "system.get_resources",
    "check_cpu": "system.get_resources",
    "check_memory": "system.get_resources",
    "check_disk": "system.get_resources",
}


STRATEGIST_SYSTEM_PROMPT = """You are the Strategist Agent for VoxOS, an autonomous AI assistant.

Your job is to produce a step-by-step execution plan given a user's intent.

You MUST respond with a valid JSON object matching this schema:
{
  "goal": "string — what we are trying to accomplish",
  "estimated_risk": "LOW | MEDIUM | HIGH",
  "reasoning": "string — brief explanation of approach",
  "steps": [
    {
      "id": "step_1",
      "action": "string — human description",
      "tool": "string | null — tool name from registry, or null if no tool needed",
      "arguments": {},
      "depends_on": [],
      "description": "string"
    }
  ]
}

Available tools: {tools}

Rules:
- Only use tools from the provided list
- Use depends_on to chain steps that must execute in order
- Independent steps (e.g. parallel searches) can have no dependencies
- Set tool=null for steps that are pure reasoning/formatting (not tool calls)
- Keep plans minimal — don't add unnecessary steps
- If a step deletes/destroys something, note it in the risk level
- IMPORTANT: If the user asks to search the web or open a URL (e.g., "open chrome and search for X"), ALWAYS use `browser.search` or `browser.open` instead of `desktop.open_app`.

For simple single-tool intents, produce a 1-step plan.
For complex multi-step tasks, decompose carefully.
"""


class StrategistAgent(BaseAgent[StrategistInput, StrategistOutput]):
    """
    Plans how to accomplish a task using available tools.
    Never executes tools.
    """

    def __init__(self) -> None:
        self._router = get_llm_router()
        self._registry = get_tool_registry()
        self._bus = get_event_bus()

    @property
    def name(self) -> str:
        return "strategist"

    async def process(self, input: StrategistInput) -> StrategistOutput:
        log.info(
            "strategist_processing",
            intent=input.intent,
            task_id=input.task_id,
            replanning=bool(input.previous_failures),
        )

        async with metrics.async_timer("agent.latency", agent=self.name):
            output = await self._create_plan(input)

        await self._bus.publish(JarvisEvent(
            event_type=EventType.PLAN_CREATED,
            payload={
                "task_id": input.task_id,
                "goal": output.plan.goal,
                "steps": len(output.plan.steps),
                "risk": output.plan.estimated_risk,
            },
            session_id=input.session_id,
            task_id=input.task_id,
            agent=self.name,
        ))

        log.info(
            "strategist_plan_created",
            task_id=input.task_id,
            steps=len(output.plan.steps),
            risk=output.plan.estimated_risk,
        )
        return output

    async def _create_plan(self, input: StrategistInput) -> StrategistOutput:
        """Create a plan, using fast-path for simple single-tool intents."""
        # Fast path: deterministic single-step plan
        tool_name = INTENT_TO_TOOL.get(input.intent)
        if tool_name and not input.previous_failures:
            tool = self._registry.get(tool_name)
            if tool:
                plan = TaskPlan(
                    goal=input.user_request,
                    estimated_risk=tool.metadata.risk_level.value,
                    reasoning=f"Direct mapping: {input.intent} → {tool_name}",
                    steps=[TaskStep(
                        action=f"Execute {tool_name}",
                        tool=tool_name,
                        arguments=self._build_arguments(input.intent, input.entities),
                        depends_on=[],
                        description=tool.metadata.description,
                    )],
                )
                log.debug("strategist_fast_path", intent=input.intent, tool=tool_name)
                return StrategistOutput(plan=plan, reasoning="Fast-path single tool")

        # LLM planning for complex or unknown intents
        return await self._llm_plan(input)

    async def _llm_plan(self, input: StrategistInput) -> StrategistOutput:
        """Use LLM to create a multi-step plan."""
        tool_list = ", ".join(m.name for m in self._registry.list_tools())

        failure_context = ""
        if input.previous_failures:
            failure_context = f"\n\nPREVIOUS FAILURES (avoid these approaches):\n" + "\n".join(
                f"- {f}" for f in input.previous_failures
            )

        system = STRATEGIST_SYSTEM_PROMPT.replace("{tools}", tool_list)
        messages = [
            LLMMessage(
                role="user",
                content=f"Intent: {input.intent}\nEntities: {json.dumps(input.entities)}\n"
                        f"User request: {input.user_request}{failure_context}",
            )
        ]

        # Complex planning uses the strong model
        complexity = TaskComplexity.COMPLEX if len(input.previous_failures) > 0 else TaskComplexity.STANDARD
        config = self._router.config_for(
            complexity=complexity,
            response_format={"type": "json_object"},
            system_prompt=system,
            temperature=0.1,
            max_tokens=2048,
        )

        response = await self._router.provider.complete(messages, config)

        try:
            data = json.loads(response.content)
            steps = [
                TaskStep(
                    id=s.get("id", f"step_{i+1}"),
                    action=s.get("action", ""),
                    tool=s.get("tool"),
                    arguments=s.get("arguments", {}),
                    depends_on=s.get("depends_on", []),
                    description=s.get("description", ""),
                )
                for i, s in enumerate(data.get("steps", []))
            ]
            plan = TaskPlan(
                goal=data.get("goal", input.user_request),
                estimated_risk=data.get("estimated_risk", "MEDIUM"),
                reasoning=data.get("reasoning", ""),
                steps=steps,
            )
            return StrategistOutput(plan=plan, reasoning=data.get("reasoning", ""))
        except (json.JSONDecodeError, KeyError) as e:
            log.error("strategist_parse_error", error=str(e), content=response.content[:500])
            # Fallback: single conversational step
            plan = TaskPlan(
                goal=input.user_request,
                estimated_risk="LOW",
                steps=[TaskStep(
                    action="respond_conversationally",
                    tool=None,
                    description="Could not plan — respond directly to user",
                )],
            )
            return StrategistOutput(plan=plan, reasoning="Fallback: parse error")

    @staticmethod
    def _build_arguments(intent: str, entities: dict[str, Any]) -> dict[str, Any]:
        """Build tool arguments from intent + entities."""
        if intent == "get_current_time":
            tz = entities.get("timezone", "UTC")
            # Map common abbreviations
            TZ_MAP = {"IST": "Asia/Kolkata", "EST": "US/Eastern", "PST": "US/Pacific"}
            return {"timezone_name": TZ_MAP.get(tz, tz)}
        if intent in {"get_resources", "check_cpu", "check_memory"}:
            return {"include_processes": entities.get("include_processes", False)}
        return {}
