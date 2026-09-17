"""Experiment 5 — Failure recovery through replanning.

Loop under test (voxos/core/task_manager/manager.py):

  plan → execute → step fails → Overseer captures failure
       → Strategist(previous_failures=…) → new plan → execute …  (≤ max_retries)

Faults are injected into real tools (the tool returns a retryable IO_ERROR
instead of running) so the failure is controlled and repeatable. Everything
else is real: Strategist LLM, Executor, Overseer verification, OS side effects.

The Listener is replaced with a stub that tags every request as a multi-step
task, so requests always reach the Strategist — the only path where
replanning exists. Each fault scenario is paired with a no-fault control run
of the same request to measure the latency that recovery adds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid

from common import (
    COUNTER, WORKSPACE, AutoApprover, build_agents, disable_db_sync,
    environment_info, instrument_llm, pace, pct, reset_counter, reset_workspace, save, stats,
)

REPS = 2
PACE_S = 6.0  # spacing between LLM-backed runs (Groq free-tier TPM)
WS = WORKSPACE


class StubListener:
    async def process(self, inp):
        from voxos.agents.listener.agent import ListenerOutput

        return ListenerOutput(intent="multi_step_task", language="en", entities={},
                              requires_execution=True, confidence=1.0, raw_text=inp.text)


class FaultInjector:
    """Makes a registry tool fail its first `n` calls (n=-1: every call)."""

    def __init__(self) -> None:
        self._originals = {}
        self.injected = 0

    def arm(self, tool_name: str, n: int) -> None:
        from voxos.tools.base import ToolResult
        from voxos.tools.registry import get_tool_registry

        tool = get_tool_registry().get(tool_name)
        original = tool._execute
        self._originals[tool_name] = (tool, original)
        state = {"calls": 0}

        async def faulty(args):
            state["calls"] += 1
            if n < 0 or state["calls"] <= n:
                self.injected += 1
                return ToolResult.fail(error=f"{tool_name}: simulated I/O error (device busy)",
                                       error_code="IO_ERROR")
            return await original(args)

        tool._execute = faulty

    def disarm(self) -> None:
        for tool, original in self._originals.values():
            tool._execute = original
        self._originals.clear()
        self.injected = 0


def clean_ws():
    for p in WS.iterdir():
        if p.name in {"notes.txt"}:
            continue
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    (WS / "notes.txt").write_text("VoxOS recovery seed note", encoding="utf-8")


def text(p):
    return p.read_text(encoding="utf-8", errors="ignore").lower() if p.is_file() else ""


SCENARIOS = [
    {"id": "R1", "name": "Transient write fault (1×)",
     "request": f"Create a file at {WS}\\r1.txt containing recovery test one",
     "fault": ("os.write_file", 1), "expect": "recover",
     "check": lambda: "recovery test one" in text(WS / "r1.txt")},
    {"id": "R2", "name": "Transient write fault (2×)",
     "request": f"Create a file at {WS}\\r2.txt containing recovery test two",
     "fault": ("os.write_file", 2), "expect": "recover",
     "check": lambda: "recovery test two" in text(WS / "r2.txt")},
    {"id": "R3", "name": "Transient folder fault in multi-step plan",
     "request": f"Create a folder {WS}\\r3dir and then create a file {WS}\\r3dir\\data.txt containing nested ok",
     "fault": ("os.create_folder", 1), "expect": "recover",
     "check": lambda: "nested ok" in text(WS / "r3dir" / "data.txt")},
    {"id": "R4", "name": "Transient read fault",
     "request": f"Read the file {WS}\\notes.txt and tell me what it says",
     "fault": ("os.read_file", 1), "expect": "recover",
     "check": None},  # judged by COMPLETED + note text in result
    {"id": "R5", "name": "Persistent write fault (tool unusable)",
     "request": f"Create a file at {WS}\\r5.txt containing alternative route",
     "fault": ("os.write_file", -1), "expect": "alternative_or_fail",
     "check": lambda: "alternative route" in text(WS / "r5.txt")},
    {"id": "R6", "name": "Non-retryable: file does not exist",
     "request": f"Read the file {WS}\\does_not_exist.txt",
     "fault": None, "expect": "fail_fast",
     "check": lambda: False},
]


async def run_one(tm, agents, scn, injector, with_fault):
    clean_ws()
    if with_fault and scn["fault"]:
        injector.arm(*scn["fault"])
    reset_counter()
    task = await tm.submit_task(scn["request"], str(uuid.uuid4()))
    plans = []
    orig_process = agents[1].process

    async def spy(inp):
        out = await orig_process(inp)
        plans.append({"replan": bool(inp.previous_failures),
                      "failures_given": inp.previous_failures,
                      "tools": [s.tool for s in out.plan.steps]})
        return out

    agents[1].process = spy
    t0 = time.perf_counter()
    try:
        task = await tm.run_pipeline(task, *agents)
    finally:
        agents[1].process = orig_process
    ms = (time.perf_counter() - t0) * 1000
    injected = injector.injected
    injector.disarm()

    blob = json.dumps(task.result or {}, default=str).lower()
    completed = task.status.value == "COMPLETED"
    if scn["check"] is None:
        verified = completed and "recovery seed note" in blob
    else:
        verified = bool(scn["check"]())
    fault_tools = {scn["fault"][0]} if scn["fault"] else set()
    alt_plan = any(p["replan"] and not (set(p["tools"]) & fault_tools) for p in plans)
    step_errors = [s.error for s in (task.plan.steps if task.plan else []) if s.error]
    return {
        "with_fault": with_fault,
        "status": task.status.value,
        "verified_success": verified,
        "initial_success": verified and task.retry_count == 0,
        "recovered": verified and task.retry_count > 0,
        "replans": task.retry_count,
        "faults_injected": injected,
        "alternative_plan": alt_plan,
        "plans": plans,
        "latency_ms": ms - COUNTER.rate_limit_wait_ms,
        "throttle_wait_ms": round(COUNTER.rate_limit_wait_ms, 1),
        "llm_calls": COUNTER.calls,
        "tokens": COUNTER.total_tokens,
        "rate_limited": COUNTER.rate_limited,
        "error_info": task.error_info,
        "step_errors": step_errors[-3:],
    }


async def main():
    reset_workspace()
    os.chdir(WS)
    disable_db_sync()
    instrument_llm()
    from voxos.core.task_manager.manager import TaskManager

    _, strategist, executor, overseer = build_agents()
    agents = [StubListener(), strategist, executor, overseer]
    approver = AutoApprover(approve=True)
    approver.start()
    tm = TaskManager()
    injector = FaultInjector()

    out = []
    for scn in SCENARIOS:
        controls, faults = [], []
        for rep in range(REPS):
            if scn["fault"]:
                await pace(PACE_S)
                c = await run_one(tm, agents, scn, injector, with_fault=False)
                controls.append(c)
            await pace(PACE_S)
            f = await run_one(tm, agents, scn, injector, with_fault=True)
            faults.append(f)
            print(f"{scn['id']} rep{rep} fault-run: status={f['status']:9} verified={f['verified_success']!s:5} "
                  f"replans={f['replans']} injected={f['faults_injected']} alt={f['alternative_plan']} "
                  f"{f['latency_ms']:7.0f}ms calls={f['llm_calls']} 429s={f['rate_limited']}"
                  + (f" | control: {c['status']} {c['verified_success']} {c['latency_ms']:.0f}ms" if scn["fault"] else ""))
            if not f["verified_success"]:
                print("      plans:", [p["tools"] for p in f["plans"]], "errors:", str(f["step_errors"])[:250])

        ctrl_ok = [c for c in controls if c["verified_success"]]
        rec = [f for f in faults if f["recovered"]]
        # latency comparisons ignore runs delayed by provider throttling
        ctrl_lat = [c for c in ctrl_ok if not c["rate_limited"]]
        rec_lat = [f for f in rec if not f["rate_limited"]]
        row = {
            "id": scn["id"],
            "scenario": scn["name"],
            "expected": scn["expect"],
            "reps": REPS,
            "control_success": f"{len(ctrl_ok)}/{len(controls)}" if controls else "n/a",
            "initial_success": sum(f["initial_success"] for f in faults),
            "recovery_success": len(rec),
            "recovery_failure": sum(not f["verified_success"] for f in faults),
            "mean_replans": round(sum(f["replans"] for f in faults) / REPS, 2),
            "alternative_plans": sum(f["alternative_plan"] for f in faults),
            "fault_latency_ms": stats([f["latency_ms"] for f in faults]),
            "control_latency_ms": stats([c["latency_ms"] for c in ctrl_ok]) if ctrl_ok else None,
            "added_latency_ms": (round(stats([f["latency_ms"] for f in rec_lat])["mean"]
                                       - stats([c["latency_ms"] for c in ctrl_lat])["mean"], 1)
                                 if rec_lat and ctrl_lat else None),
            "rate_limited_runs": sum(bool(x["rate_limited"]) for x in faults + controls),
            "mean_llm_calls_fault": round(sum(f["llm_calls"] for f in faults) / REPS, 2),
            "mean_llm_calls_control": round(sum(c["llm_calls"] for c in controls) / len(controls), 2) if controls else None,
            "runs": {"fault": faults, "control": controls},
        }
        out.append(row)

    await approver.stop()

    fault_scn = [r for r in out if r["expected"] in {"recover", "alternative_or_fail"}]
    n = sum(r["reps"] for r in fault_scn)
    rec_scn = [r for r in out if r["expected"] == "recover"]
    fail_fast = next(r for r in out if r["expected"] == "fail_fast")
    summary = {
        "fault_runs": n,
        "initial_success": sum(r["initial_success"] for r in fault_scn),
        "recovery_success": sum(r["recovery_success"] for r in fault_scn),
        "recovery_failure": sum(r["recovery_failure"] for r in fault_scn),
        "recovery_rate_pct": pct(sum(r["recovery_success"] for r in fault_scn), n),
        "transient_fault_recovery_rate_pct": pct(sum(r["recovery_success"] for r in rec_scn), sum(r["reps"] for r in rec_scn)),
        "mean_replans_when_recovered": round(
            sum(f["replans"] for r in fault_scn for f in r["runs"]["fault"] if f["recovered"])
            / max(1, sum(r["recovery_success"] for r in fault_scn)), 2),
        "mean_added_latency_ms": round(
            sum(r["added_latency_ms"] for r in fault_scn if r["added_latency_ms"] is not None)
            / max(1, sum(1 for r in fault_scn if r["added_latency_ms"] is not None)), 1),
        "non_retryable_fail_fast_correct": f"{sum(f['replans'] == 0 and f['status'] == 'FAILED' for f in fail_fast['runs']['fault'])}/{fail_fast['reps']}",
    }
    table = [{k: v for k, v in r.items() if k != "runs"} for r in out]
    print(json.dumps({"summary": summary, "scenarios": table}, indent=2))
    save("exp5_failure_recovery", {"environment": environment_info(), "summary": summary, "scenarios": out})


if __name__ == "__main__":
    asyncio.run(main())
