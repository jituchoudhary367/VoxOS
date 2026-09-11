"""
Task decomposition helpers for the Strategist Agent.

Breaks complex multi-step user requests into ordered, dependency-aware steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from voxos.core.task_manager.models import TaskPlan, TaskStep


@dataclass
class DecompositionHint:
    """
    A hint about how to decompose a complex intent into sub-tasks.
    """
    primary_tool: str | None
    pre_steps: list[dict[str, Any]] = field(default_factory=list)
    post_steps: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Compound intent decomposition rules
# ─────────────────────────────────────────────────────────────────────────────

DECOMPOSITION_RULES: dict[str, DecompositionHint] = {
    "research_topic": DecompositionHint(
        primary_tool="web.search",
        post_steps=[
            {"action": "Extract key information", "tool": "web.extract"},
            {"action": "Summarize findings", "tool": None},
        ],
    ),
    "create_report": DecompositionHint(
        primary_tool=None,
        pre_steps=[
            {"action": "Search for information", "tool": "web.search"},
        ],
    ),
    "browse_and_fill_form": DecompositionHint(
        primary_tool="browser.open",
        post_steps=[
            {"action": "Fill form fields", "tool": "browser.type"},
            {"action": "Submit form", "tool": "browser.click"},
        ],
    ),
}


def resolve_dependencies(steps: list[TaskStep]) -> list[TaskStep]:
    """
    Automatically resolve dependencies for a linear step list.

    For a simple ordered plan where step[i] depends on step[i-1],
    this auto-fills the depends_on list.
    """
    if len(steps) <= 1:
        return steps

    resolved = [steps[0]]
    for i in range(1, len(steps)):
        step = steps[i]
        if not step.depends_on:
            # Default: depend on the immediately prior step
            step = step.model_copy(update={"depends_on": [steps[i - 1].id]})
        resolved.append(step)
    return resolved


def validate_plan(plan: TaskPlan) -> list[str]:
    """
    Validate a plan for logical consistency.

    Returns a list of validation errors (empty if plan is valid).
    """
    errors: list[str] = []
    step_ids = {s.id for s in plan.steps}

    for step in plan.steps:
        # Check that all dependencies exist
        for dep_id in step.depends_on:
            if dep_id not in step_ids:
                errors.append(
                    f"Step '{step.id}' depends on '{dep_id}' which doesn't exist in the plan"
                )

        # Check that tool name is specified for execution steps
        if step.tool is None and not step.description:
            errors.append(f"Step '{step.id}' has no tool and no description")

    # Check for circular dependencies (simple DFS)
    if _has_cycle(plan.steps):
        errors.append("Plan contains a circular dependency")

    return errors


def _has_cycle(steps: list[TaskStep]) -> bool:
    """Detect cycles in the step dependency graph using DFS."""
    adj: dict[str, list[str]] = {s.id: list(s.depends_on) for s in steps}
    visited: set[str] = set()
    path: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        path.add(node)
        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in path:
                return True
        path.discard(node)
        return False

    for step_id in adj:
        if step_id not in visited:
            if dfs(step_id):
                return True
    return False


def estimate_complexity(plan: TaskPlan) -> str:
    """
    Estimate plan complexity for routing (SIMPLE | MODERATE | COMPLEX).
    """
    step_count = len(plan.steps)
    has_high_risk = plan.estimated_risk in {"HIGH", "CRITICAL"}
    if step_count <= 1 and not has_high_risk:
        return "SIMPLE"
    elif step_count <= 3 and not has_high_risk:
        return "MODERATE"
    else:
        return "COMPLEX"
