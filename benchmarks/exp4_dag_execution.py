"""Experiment 4 — Planning / dependency (DAG) execution.

Hand-built plans are fed to the real ExecutorAgent (registry lookup, permission
check, argument validation, risk engine all active). Each step calls the
instrumented `bench.work` tool, so the order and overlap of steps can be read
from wall-clock timestamps. No LLM is involved: this isolates the executor.

Properties checked per run:
  * dependencies respected   — every step starts after all its deps finished
  * concurrency              — steps with no mutual dependency overlap in time
  * dependent waiting        — a join step waits for its slowest dependency
  * failure propagation      — downstream of a failed step is SKIPPED, never run;
                               unrelated branches still run
"""

from __future__ import annotations

import asyncio
import json
import time

from bench_tool import TIMELINE, register_bench_tool, reset_timeline
from common import environment_info, pct, save, stats

REPS = 5
D = 0.30  # seconds per unit of work
EPS = 0.005


def step(sid, deps=(), duration=D, fail=False):
    return {"id": sid, "deps": list(deps), "duration": duration, "fail": fail}


SCENARIOS = [
    {"name": "S1 Chain A→B→C", "topology": "A→B→C",
     "steps": [step("A"), step("B", ["A"]), step("C", ["B"])]},
    {"name": "S2 Fan-in A,B→C", "topology": "A→C, B→C",
     "steps": [step("A"), step("B"), step("C", ["A", "B"])]},
    {"name": "S3 Independent A,B,C", "topology": "A  B  C",
     "steps": [step("A"), step("B"), step("C")]},
    {"name": "S4 Diamond", "topology": "A→B, A→C, B→D, C→D",
     "steps": [step("A"), step("B", ["A"]), step("C", ["A"]), step("D", ["B", "C"])]},
    {"name": "S5 Uneven fan-in", "topology": "A(0.2s)→C, B(0.6s)→C",
     "steps": [step("A", duration=0.2), step("B", duration=0.6), step("C", ["A", "B"])]},
    {"name": "S6 Wide parallel ×8", "topology": "8 independent",
     "steps": [step(f"P{i}") for i in range(8)]},
    {"name": "F1 Chain, middle fails", "topology": "A→B✗→C",
     "steps": [step("A"), step("B", ["A"], fail=True), step("C", ["B"])]},
    {"name": "F2 Fan-in, one branch fails", "topology": "A✗→C, B→C",
     "steps": [step("A", fail=True), step("B"), step("C", ["A", "B"])]},
    {"name": "F3 Independent, one fails", "topology": "A✗  B  C",
     "steps": [step("A", fail=True), step("B"), step("C")]},
    {"name": "F4 Transitive skip", "topology": "A✗→B→C→D",
     "steps": [step("A", fail=True), step("B", ["A"]), step("C", ["B"]), step("D", ["C"])]},
    {"name": "F5 Diamond, one arm fails", "topology": "A→B✗, A→C, B→D, C→D",
     "steps": [step("A"), step("B", ["A"], fail=True), step("C", ["A"]), step("D", ["B", "C"])]},
]


def ancestors_failed(sid, spec, failed):
    by_id = {s["id"]: s for s in spec}
    stack = list(by_id[sid]["deps"])
    while stack:
        d = stack.pop()
        if d in failed:
            return True
        stack.extend(by_id[d]["deps"])
    return False


def related(a, b, spec):
    """True if a is an ancestor of b or vice versa."""
    by_id = {s["id"]: s for s in spec}

    def anc(x):
        out, stack = set(), list(by_id[x]["deps"])
        while stack:
            d = stack.pop()
            if d not in out:
                out.add(d)
                stack.extend(by_id[d]["deps"])
        return out

    return a in anc(b) or b in anc(a)


def expected_ready_waves(spec, failed):
    """Ideal level-by-level schedule: which steps can start together."""
    done, waves, remaining = set(), [], [s for s in spec]
    while remaining:
        ready = [s for s in remaining if all(d in done for d in s["deps"])]
        if not ready:
            break
        waves.append({s["id"] for s in ready})
        for s in ready:
            remaining.remove(s)
            if s["id"] not in failed:
                done.add(s["id"])
    return waves


async def run_once(scn):
    from voxos.agents.executor.agent import ExecutorAgent, ExecutorInput
    from voxos.core.task_manager.models import StepStatus, Task, TaskPlan, TaskStep

    spec = scn["steps"]
    plan = TaskPlan(goal=scn["name"], steps=[
        TaskStep(id=s["id"], action=f"work {s['id']}", tool="bench.work",
                 arguments={"label": s["id"], "duration_s": s["duration"], "fail": s["fail"]},
                 depends_on=s["deps"])
        for s in spec
    ])
    task = Task(session_id="bench", user_request=scn["name"], plan=plan)
    reset_timeline()
    t0 = time.perf_counter()
    out = await ExecutorAgent().process(ExecutorInput(task=task))
    wall = time.perf_counter() - t0

    tl = {e["label"]: e for e in TIMELINE}
    status = {s.id: s.status for s in plan.steps}
    injected = {s["id"] for s in spec if s["fail"]}
    by_id = {s["id"]: s for s in spec}

    # 1. dependencies respected
    dep_viol = [
        f"{s['id']} started before {d} finished"
        for s in spec if s["id"] in tl
        for d in s["deps"]
        if d not in tl or tl[s["id"]]["start"] + EPS < tl[d]["end"]
    ]
    # 2. concurrency: executed, mutually-independent pairs that became ready in
    #    the same wave must overlap in time
    waves = expected_ready_waves(spec, injected)
    conc_pairs = conc_ok = 0
    for wave in waves:
        ids = sorted(i for i in wave if i in tl)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if related(a, b, spec):
                    continue
                conc_pairs += 1
                ea, eb = tl[a], tl[b]
                if min(ea["end"], eb["end"]) - max(ea["start"], eb["start"]) > 0:
                    conc_ok += 1
    # 3. failure handling
    should_skip = {s["id"] for s in spec if s["id"] not in injected and ancestors_failed(s["id"], spec, injected)}
    should_run = {s["id"] for s in spec} - should_skip
    skipped_wrongly_ran = sorted(i for i in should_skip if i in tl)
    skipped_not_marked = sorted(i for i in should_skip if status[i] != StepStatus.SKIPPED)
    healthy_not_run = sorted(i for i in should_run if i not in tl)
    failed_not_marked = sorted(i for i in injected if status[i] != StepStatus.FAILED)
    completed_ok = all(status[i] == StepStatus.COMPLETED for i in should_run - injected)

    sequential = sum(by_id[i]["duration"] for i in tl)
    return {
        "wall_s": wall,
        "sequential_s": sequential,
        "speedup": sequential / wall if wall else 0,
        "deps_respected": not dep_viol,
        "dep_violations": dep_viol,
        "concurrent_pairs": conc_pairs,
        "concurrent_pairs_overlapping": conc_ok,
        "failure_handling_correct": not (skipped_wrongly_ran or skipped_not_marked or healthy_not_run or failed_not_marked) and completed_ok,
        "detail": {
            "skipped_but_ran": skipped_wrongly_ran,
            "should_skip_not_marked": skipped_not_marked,
            "healthy_not_run": healthy_not_run,
            "failed_not_marked": failed_not_marked,
            "status": {k: v.value for k, v in status.items()},
            "executor_failed": out.failed_steps,
            "executor_skipped": out.skipped_steps,
        },
    }


async def main():
    register_bench_tool()
    results = []
    for scn in SCENARIOS:
        runs = [await run_once(scn) for _ in range(REPS)]
        pairs = sum(r["concurrent_pairs"] for r in runs)
        overl = sum(r["concurrent_pairs_overlapping"] for r in runs)
        row = {
            "scenario": scn["name"],
            "topology": scn["topology"],
            "reps": REPS,
            "deps_respected": f"{sum(r['deps_respected'] for r in runs)}/{REPS}",
            "concurrency": f"{overl}/{pairs}" if pairs else "n/a",
            "failure_handling": f"{sum(r['failure_handling_correct'] for r in runs)}/{REPS}",
            "wall_s": stats([r["wall_s"] for r in runs]),
            "sequential_s": round(runs[0]["sequential_s"], 2),
            "speedup_mean": round(sum(r["speedup"] for r in runs) / REPS, 2),
            "last_run_detail": runs[-1]["detail"],
            "violations": sorted({v for r in runs for v in r["dep_violations"]}),
        }
        results.append(row)
        print(f"{row['scenario']:30} deps={row['deps_respected']} conc={row['concurrency']:6} "
              f"fail_ok={row['failure_handling']} wall={row['wall_s']['mean']:.3f}s "
              f"seq={row['sequential_s']:.2f}s speedup={row['speedup_mean']}x "
              f"status={row['last_run_detail']['status']}")

    total = len(SCENARIOS) * REPS
    summary = {
        "scenarios": len(SCENARIOS),
        "runs": total,
        "dependency_order_respected_pct": pct(sum(int(r["deps_respected"].split("/")[0]) for r in results), total),
        "independent_pairs_run_concurrently_pct": pct(
            sum(int(r["concurrency"].split("/")[0]) for r in results if r["concurrency"] != "n/a"),
            sum(int(r["concurrency"].split("/")[1]) for r in results if r["concurrency"] != "n/a")),
        "failure_propagation_correct_pct": pct(sum(int(r["failure_handling"].split("/")[0]) for r in results), total),
    }
    print(json.dumps(summary, indent=2))
    save("exp4_dag_execution", {"environment": environment_info(), "summary": summary, "scenarios": results})


if __name__ == "__main__":
    asyncio.run(main())
