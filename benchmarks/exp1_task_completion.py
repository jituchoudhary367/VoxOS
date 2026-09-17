"""Experiment 1 — Task-completion reliability.

20 natural-language OS tasks run through the full pipeline
(Listener → Router → Strategist → Executor → Overseer).

A task counts as successful only if the pipeline reports COMPLETED *and* an
independent ground-truth check of the final OS state passes. A COMPLETED task
whose check fails is recorded as a false completion.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import psutil

from common import (
    COUNTER, WORKSPACE, AutoApprover, build_agents, disable_db_sync,
    environment_info, instrument_llm, pace, pct, reset_counter, reset_workspace, save, stats,
)

WS = str(WORKSPACE)
REPS = 3
PACE_S = 3.0  # between tasks; 429s are waited out in common.instrument_llm


def blob(task) -> str:
    return json.dumps(task.result or {}, default=str).lower()


def ok(task) -> bool:
    return task.status.value == "COMPLETED"


def proc_running(name: str) -> bool:
    return any((p.info["name"] or "").lower() == name for p in psutil.process_iter(["name"]))


def proc_exits(name: str, timeout_s: float = 3.0) -> bool:
    """Windows 11 Notepad's process outlives its window by about a second."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not proc_running(name):
            return True
        time.sleep(0.2)
    return not proc_running(name)


def seed(ws: Path) -> None:
    (ws / "notes.txt").write_text("VoxOS benchmark seed note", encoding="utf-8")
    (ws / "report.md").write_text("# Quarterly report", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print('app')", encoding="utf-8")
    (ws / "src" / "utils.py").write_text("def util(): pass", encoding="utf-8")
    (ws / "data").mkdir()
    (ws / "data" / "sample.csv").write_text("id,name\n1,alpha\n2,beta\n", encoding="utf-8")


W = Path(WS)
TASKS = [
    # id, category, complexity, request, ground-truth check
    ("T1", "Information", "simple", "What time is it?",
     lambda t: ok(t) and ("time" in blob(t) or ":" in blob(t))),
    ("T2", "Information", "simple", "What operating system am I running?",
     lambda t: ok(t) and "windows" in blob(t)),
    ("T3", "Information", "simple", "How much RAM is being used right now?",
     lambda t: ok(t) and ("memory" in blob(t) or "ram" in blob(t))),
    ("T4", "File system", "simple", f"List files in {WS}",
     lambda t: ok(t) and "notes.txt" in blob(t)),
    ("T5", "File system", "simple", f"Find all .py files in {WS}",
     lambda t: ok(t) and "app.py" in blob(t) and "utils.py" in blob(t)),
    ("T6", "File operations", "simple", f"Create a folder called reports in {WS}",
     lambda t: (W / "reports").is_dir()),
    ("T7", "File operations", "simple", f"Write a file at {WS}\\todo.txt with the text buy milk",
     lambda t: (W / "todo.txt").is_file() and "buy milk" in (W / "todo.txt").read_text().lower()),
    ("T8", "File operations", "simple", f"Read the file {WS}\\notes.txt",
     lambda t: ok(t) and "benchmark seed note" in blob(t)),
    ("T9", "File operations", "simple", f"Copy {WS}\\notes.txt to {WS}\\notes_backup.txt",
     lambda t: (W / "notes_backup.txt").is_file()),
    ("T10", "File operations", "simple", f"Rename {WS}\\report.md to final_report.md",
     lambda t: (W / "final_report.md").is_file() and not (W / "report.md").exists()),
    ("T11", "Application", "simple", "Open Notepad",
     lambda t: proc_running("notepad.exe")),
    ("T12", "Application", "simple", "What applications are currently running?",
     lambda t: ok(t) and len(blob(t)) > 50),
    ("T13", "Application", "simple", "Close Notepad",
     lambda t: proc_exits("notepad.exe")),
    ("T14", "Browser", "simple", "Open github.com in the browser",
     lambda t: ok(t) and "github.com" in blob(t)),
    ("T15", "Browser", "simple", "Search for Python asyncio tutorial",
     lambda t: ok(t) and "asyncio" in blob(t)),
    ("T16", "Multi-step", "complex",
     f"Find the file named sample.csv in {WS} and then read its contents",
     lambda t: ok(t) and "alpha" in blob(t)),
    ("T17", "Multi-step", "complex",
     f"Create a folder named project in {WS}, then create a file readme.txt inside it "
     f"containing Hello VoxOS, then read the file back",
     lambda t: (W / "project" / "readme.txt").is_file()
     and "hello voxos" in (W / "project" / "readme.txt").read_text().lower()
     and ok(t) and "hello voxos" in blob(t)),
    ("T18", "Multi-step", "complex",
     f"Create a folder named archive in {WS} and then copy {WS}\\notes.txt into that archive folder",
     lambda t: (W / "archive" / "notes.txt").is_file()),
    ("T19", "Parallel", "complex",
     "Tell me the current time, my system information, and my RAM usage",
     lambda t: ok(t) and "windows" in blob(t) and ("memory" in blob(t) or "ram" in blob(t))),
    ("T20", "Parallel", "complex",
     f"Create two files in {WS}: a.txt containing alpha and b.txt containing beta",
     lambda t: (W / "a.txt").is_file() and (W / "b.txt").is_file()
     and "alpha" in (W / "a.txt").read_text() and "beta" in (W / "b.txt").read_text()),
]


async def main() -> None:
    ws = reset_workspace()
    seed(ws)
    os.chdir(ws)  # relative paths the planner emits resolve inside the sandbox
    disable_db_sync()
    instrument_llm()

    from voxos.core.task_manager.manager import TaskManager
    from voxos.core.task_manager.models import StepStatus

    listener, strategist, executor, overseer = build_agents()
    approver = AutoApprover(approve=True)
    approver.start()
    tm = TaskManager()
    session = str(uuid.uuid4())
    rows = []

    for rep in range(REPS):
        if rep:
            os.chdir(Path.home())  # Windows cannot delete the current directory
            seed(reset_workspace())
            os.chdir(WORKSPACE)
        for tid, cat, complexity, request, check in TASKS:
            await pace(PACE_S)
            reset_counter()
            approvals_before = len(approver.seen)
            task = await tm.submit_task(request, session)
            t0 = time.perf_counter()
            task = await tm.run_pipeline(task, listener, strategist, executor, overseer)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(0.5)  # let app launches/closes settle before checking
            try:
                verified = bool(check(task))
            except Exception:  # noqa: BLE001
                verified = False

            steps = task.plan.steps if task.plan else []
            done = sum(1 for s in steps if s.status == StepStatus.COMPLETED)
            row = {
                "rep": rep,
                "id": tid,
                "category": cat,
                "complexity": complexity,
                "request": request.replace(WS, "<WS>"),
                "execution_class": task.execution_class,
                "escalated_from": task.escalated_from,
                "intent": task.intent,
                "steps_planned": len(steps),
                "steps_completed": done,
                "tools": [s.tool for s in steps],
                "pipeline_status": task.status.value,
                "verified_success": verified,
                "false_completion": task.status.value == "COMPLETED" and not verified,
                "replanned": task.retry_count > 0,
                "replans": task.retry_count,
                "approvals": len(approver.seen) - approvals_before,
                "time_s": round(elapsed - COUNTER.rate_limit_wait_ms / 1000, 2),
                "throttle_wait_s": round(COUNTER.rate_limit_wait_ms / 1000, 2),
                "llm": COUNTER.snapshot(),
                "errors": [s.error for s in steps if s.error] or task.error_info,
            }
            rows.append(row)
            print(f"rep{rep} {tid:4} {cat:16} {row['execution_class'] or '-':10} steps={done}/{len(steps)} "
                  f"status={task.status.value:9} verified={verified!s:5} replans={task.retry_count} "
                  f"{elapsed:6.2f}s llm_calls={COUNTER.calls} 429s={COUNTER.rate_limited}", flush=True)
            if not verified:
                print("      errors:", str(row["errors"])[:300])

    await approver.stop()
    try:
        from voxos.tools.browser.tools import _close_browser_session
        await _close_browser_session()
    except Exception:  # noqa: BLE001
        pass

    n = len(rows)
    succ = sum(r["verified_success"] for r in rows)
    planned = sum(r["steps_planned"] for r in rows)
    completed = sum(r["steps_completed"] for r in rows)
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    summary = {
        "runs": n,
        "successful_tasks": succ,
        "task_success_rate_pct": pct(succ, n),
        "steps_planned": planned,
        "steps_completed": completed,
        "step_completion_rate_pct": pct(completed, planned),
        "false_completions": sum(r["false_completion"] for r in rows),
        "tasks_replanned": sum(r["replanned"] for r in rows),
        "replanning_rate_pct": pct(sum(r["replanned"] for r in rows), n),
        "mean_time_s": round(sum(r["time_s"] for r in rows) / n, 2),
        "simple_success_pct": pct(sum(r["verified_success"] for r in rows if r["complexity"] == "simple"),
                                  sum(1 for r in rows if r["complexity"] == "simple")),
        "complex_success_pct": pct(sum(r["verified_success"] for r in rows if r["complexity"] == "complex"),
                                   sum(1 for r in rows if r["complexity"] == "complex")),
        "by_category": {c: f"{sum(x['verified_success'] for x in v)}/{len(v)}" for c, v in by_cat.items()},
        "total_llm_calls": sum(r["llm"]["calls"] for r in rows),
        "total_tokens": sum(r["llm"]["total_tokens"] for r in rows),
    }
    per_task = []
    for tid, cat, complexity, request, _ in TASKS:
        runs = [r for r in rows if r["id"] == tid]
        ok_runs = [r for r in runs if r["verified_success"]]
        per_task.append({
            "id": tid, "category": cat, "complexity": complexity, "request": request.replace(WS, "<WS>"),
            "routes": sorted({r["execution_class"] or "-" for r in runs}),
            "steps_planned_median": stats([r["steps_planned"] for r in runs])["median"],
            "steps": f"{sum(r['steps_completed'] for r in runs)}/{sum(r['steps_planned'] for r in runs)}",
            "success": f"{len(ok_runs)}/{len(runs)}",
            "success_rate_pct": pct(len(ok_runs), len(runs)),
            "replanned_runs": sum(r["replanned"] for r in runs),
            "false_completions": sum(r["false_completion"] for r in runs),
            "mean_time_s": round(sum(r["time_s"] for r in runs) / len(runs), 2),
            "rate_limited_runs": sum(bool(r["llm"]["rate_limited"]) for r in runs),
        })
    summary["reps"] = REPS
    summary["runs_with_rate_limit"] = sum(bool(r["llm"]["rate_limited"]) for r in rows)
    summary["tasks_successful_all_reps"] = sum(t["success"] == f"{REPS}/{REPS}" for t in per_task)
    print(json.dumps(summary, indent=2))
    save("exp1_task_completion", {"environment": environment_info(), "summary": summary,
                                  "per_task": per_task, "tasks": rows})


if __name__ == "__main__":
    asyncio.run(main())
