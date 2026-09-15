"""
Plan generation helpers for the Strategist Agent.

Contains the planning prompt templates and plan validation logic.
"""

from __future__ import annotations

import json
from typing import Any

from voxos.core.task_manager.models import TaskPlan, TaskStep
from voxos.observability.logger import get_logger

log = get_logger(__name__)


PLANNING_SYSTEM_PROMPT = """You are the Strategist Agent for VoxOS, an autonomous AI assistant.

Your job is to create a PRECISE, EXECUTABLE step-by-step plan to accomplish the user's intent.

Rules:
1. Use ONLY tools from the available tool list
2. Each step must have a specific tool and arguments
3. Mark dependencies (step B depends on step A → depends_on: ["step_A_id"])
4. Estimate risk level for the overall plan: LOW, MEDIUM, HIGH, or CRITICAL
5. Keep plans minimal — don't add unnecessary steps
6. For simple intents (get_time, get_info), use a single step

Response format (JSON only, no explanation):
{
  "goal": "Clear description of what we're accomplishing",
  "estimated_risk": "LOW|MEDIUM|HIGH|CRITICAL",
  "reasoning": "Brief explanation of the plan",
  "steps": [
    {
      "id": "step_1",
      "action": "Human-readable action description",
      "description": "What this step does",
      "tool": "tool.name",
      "arguments": {"key": "value"},
      "depends_on": []
    }
  ]
}

Available tools: {tool_list}

If previous attempts failed, learn from the failures and adjust the plan.
Previous failures: {previous_failures}
"""


def build_planning_prompt(
    intent: str,
    entities: dict[str, Any],
    user_request: str,
    tool_list: list[str],
    previous_failures: list[str] | None = None,
    language: str = "en",
) -> str:
    """Build the user message for the planning LLM call."""
    failures_str = "\n".join(previous_failures) if previous_failures else "None"
    tools_str = "\n".join(f"- {t}" for t in tool_list)

    return (
        f"User request: {user_request}\n"
        f"Detected intent: {intent}\n"
        f"Extracted entities: {json.dumps(entities)}\n"
        f"Language: {language}\n\n"
        f"Please create an execution plan.\n\n"
        f"Available tools:\n{tools_str}\n\n"
        f"Previous failures (if any):\n{failures_str}"
    )


def parse_plan_from_llm(data: dict[str, Any], task_id: str | None = None) -> TaskPlan:
    """
    Parse LLM JSON output into a structured TaskPlan.

    Handles missing fields gracefully.
    """
    steps_raw = data.get("steps", [])
    steps: list[TaskStep] = []

    for i, raw_step in enumerate(steps_raw):
        step_id = raw_step.get("id") or f"step_{i + 1}"
        steps.append(TaskStep(
            id=step_id,
            action=raw_step.get("action", f"Step {i + 1}"),
            description=raw_step.get("description", ""),
            tool=raw_step.get("tool"),
            arguments=raw_step.get("arguments", {}),
            depends_on=raw_step.get("depends_on", []),
        ))

    plan = TaskPlan(
        goal=data.get("goal", "Accomplish user request"),
        steps=steps,
        estimated_risk=data.get("estimated_risk", "LOW"),
        reasoning=data.get("reasoning", ""),
    )

    log.debug(
        "plan_parsed",
        steps=len(steps),
        risk=plan.estimated_risk,
        task_id=task_id,
    )
    return plan


def build_fallback_plan(intent: str, entities: dict[str, Any]) -> TaskPlan:
    """
    Build a minimal fallback plan when LLM planning fails.

    Maps simple intents to single-tool plans deterministically.
    """
    INTENT_TOOL_MAP: dict[str, tuple[str, dict[str, Any]]] = {
        "get_current_time": ("system.get_time", {}),
        "get_system_info": ("system.get_info", {}),
        "get_resources": ("system.get_resources", {}),
        "take_screenshot": ("desktop.screenshot", {}),
        "list_files": ("os.list_dir", {"dir_path": entities.get("directory", ".")}),
        "open_application": (
            "desktop.open_app",
            {"app_path_or_name": entities.get("application", "")},
        ),
        "close_application": (
            "desktop.close_app",
            {"window_title": entities.get("application", "")},
        ),
        "search_web": ("web.search", {"query": entities.get("query", "")}),
        "browse_url": ("browser.open", {"url": entities.get("url", "")}),
        "read_file": ("os.read_file", {"file_path": entities.get("file_path", "")}),
        "search_files": (
            "os.search_files",
            {
                "search_path": entities.get("search_path", "home"),
                "pattern": entities.get("pattern", "*"),
                "modified_within_days": entities.get("modified_within_days")
            }
        ),
    }

    if intent in INTENT_TOOL_MAP:
        tool_name, args = INTENT_TOOL_MAP[intent]
        return TaskPlan(
            goal=f"Execute {intent}",
            steps=[TaskStep(
                id="step_1",
                action=intent.replace("_", " ").title(),
                tool=tool_name,
                arguments=args,
            )],
            estimated_risk="LOW",
            reasoning="Fallback deterministic plan",
        )

    # Generic fallback — no tool, just respond
    return TaskPlan(
        goal="Respond to user",
        steps=[],
        estimated_risk="LOW",
        reasoning="No executable plan found — responding directly",
    )
