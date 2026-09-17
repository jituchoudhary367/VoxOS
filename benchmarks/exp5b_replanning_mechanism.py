"""Experiment 5b — Replanning loop in isolation.

Experiment 5 showed the LLM Strategist never produces schema-valid arguments,
so the recovery loop is never reached end-to-end. This variant replaces the
Strategist with a scripted planner that emits correct arguments, and on a
replan (previous_failures non-empty) may switch to an alternative tool.

Everything else is real: TaskManager retry loop, Executor, risk engine,
Overseer verification, fault injection, and OS side effects. This measures
whether the mechanism works — failure captured, passed back, new plan
executed — not how well an LLM replans.
"""

from __future__ import annotations

import asyncio
import json
import os

import exp5_failure_recovery as e5
from common import (
    AutoApprover, build_agents, disable_db_sync, environment_info, pct,
    reset_workspace, save, stats,
)

WS = e5.WS
REPS = 5


def plan_for(scn_id: str, replanning: bool):
    from voxos.core.task_manager.models import TaskStep

    if scn_id in {"R1", "R2"}:
        n = scn_id[1]
        word = {"1": "one", "2": "two"}[n]
        return [TaskStep(id="write", action="write", tool="os.write_file",
                         arguments={"file_path": str(WS / f"r{n}.txt"), "content": f"recovery test {word}"})]
    if scn_id == "R3":
        return [
            TaskStep(id="mkdir", action="mkdir", tool="os.create_folder", arguments={"dir_path": str(WS / "r3dir")}),
            TaskStep(id="write", action="write", tool="os.write_file", depends_on=["mkdir"],
                     arguments={"file_path": str(WS / "r3dir" / "data.txt"), "content": "nested ok"}),
        ]
    if scn_id == "R4":
        return [TaskStep(id="read", action="read", tool="os.read_file", arguments={"file_path": str(WS / "notes.txt")})]
    if scn_id == "R5":
        if replanning:  # write tool keeps failing — route around it
            return [TaskStep(id="shell_write", action="write via shell", tool="os.shell_command",
                             arguments={"command": f'cmd /c "echo alternative route> {WS / "r5.txt"}"'})]
        return [TaskStep(id="write", action="write", tool="os.write_file",
                         arguments={"file_path": str(WS / "r5.txt"), "content": "alternative route"})]
    if scn_id == "R6":
        return [TaskStep(id="read", action="read", tool="os.read_file",
                         arguments={"file_path": str(WS / "does_not_exist.txt")})]
    raise KeyError(scn_id)


class ScriptedStrategist:
    def __init__(self, scenarios):
        self._by_request = {s["request"]: s["id"] for s in scenarios}

    async def process(self, inp):
        from voxos.agents.strategist.agent import StrategistOutput
        from voxos.core.task_manager.models import TaskPlan

        sid = self._by_request[inp.user_request]
        steps = plan_for(sid, bool(inp.previous_failures))
        return StrategistOutput(plan=TaskPlan(goal=inp.user_request, steps=steps, estimated_risk="LOW"))


async def main():
    reset_workspace()
    os.chdir(WS)
    disable_db_sync()
    from voxos.core.task_manager.manager import TaskManager

    _, _, executor, overseer = build_agents()
    agents = [e5.StubListener(), ScriptedStrategist(e5.SCENARIOS), executor, overseer]
    approver = AutoApprover(approve=True)
    approver.start()
    tm = TaskManager()
    injector = e5.FaultInjector()

    out = []
    for scn in e5.SCENARIOS:
        controls, faults = [], []
        for rep in range(REPS):
            if scn["fault"]:
                controls.append(await e5.run_one(tm, agents, scn, injector, with_fault=False))
            f = await e5.run_one(tm, agents, scn, injector, with_fault=True)
            faults.append(f)
            print(f"{scn['id']} rep{rep}: status={f['status']:9} verified={f['verified_success']!s:5} "
                  f"replans={f['replans']} injected={f['faults_injected']} alt={f['alternative_plan']} "
                  f"{f['latency_ms']:7.0f}ms  plans={[p['tools'] for p in f['plans']]}"
                  + (f" | control {controls[-1]['status']} {controls[-1]['latency_ms']:.0f}ms" if controls else "")
                  + ("" if f["verified_success"] else f"  errors={str(f['step_errors'])[:160]}"), flush=True)

        ctrl_ok = [c for c in controls if c["verified_success"]]
        rec = [f for f in faults if f["recovered"]]
        forwarded = sum(1 for f in faults for p in f["plans"] if p["replan"] and p["failures_given"])
        out.append({
            "id": scn["id"],
            "scenario": scn["name"],
            "expected": scn["expect"],
            "reps": REPS,
            "control_success": f"{len(ctrl_ok)}/{len(controls)}" if controls else "n/a",
            "initial_success": sum(f["initial_success"] for f in faults),
            "recovery_success": len(rec),
            "recovery_failure": sum(not f["verified_success"] for f in faults),
            "mean_replans": round(sum(f["replans"] for f in faults) / REPS, 2),
            "replans_with_failure_context": forwarded,
            "alternative_plans": sum(f["alternative_plan"] for f in faults),
            "fault_latency_ms": stats([f["latency_ms"] for f in faults]),
            "control_latency_ms": stats([c["latency_ms"] for c in ctrl_ok]) if ctrl_ok else None,
            "added_latency_ms": (round(stats([f["latency_ms"] for f in rec])["mean"]
                                       - stats([c["latency_ms"] for c in ctrl_ok])["mean"], 1)
                                 if rec and ctrl_ok else None),
            "failures_passed_example": next((p["failures_given"] for f in faults for p in f["plans"] if p["replan"]), None),
            "runs": {"fault": faults, "control": controls},
        })

    await approver.stop()
    fault_scn = [r for r in out if r["expected"] in {"recover", "alternative_or_fail"}]
    n = sum(r["reps"] for r in fault_scn)
    rec_total = sum(r["recovery_success"] for r in fault_scn)
    fail_fast = next(r for r in out if r["expected"] == "fail_fast")
    added = [r["added_latency_ms"] for r in fault_scn if r["added_latency_ms"] is not None]
    summary = {
        "fault_runs": n,
        "initial_success": sum(r["initial_success"] for r in fault_scn),
        "recovery_success": rec_total,
        "recovery_failure": sum(r["recovery_failure"] for r in fault_scn),
        "recovery_rate_pct": pct(rec_total, n),
        "mean_replans_when_recovered": round(
            sum(f["replans"] for r in fault_scn for f in r["runs"]["fault"] if f["recovered"]) / max(1, rec_total), 2),
        "mean_added_latency_ms": round(sum(added) / len(added), 1) if added else None,
        "non_retryable_fail_fast": f"{sum(f['replans'] == 0 and f['status'] == 'FAILED' for f in fail_fast['runs']['fault'])}/{fail_fast['reps']}",
        "non_retryable_error": fail_fast["runs"]["fault"][0]["step_errors"],
    }
    table = [{k: v for k, v in r.items() if k != "runs"} for r in out]
    print(json.dumps({"summary": summary, "scenarios": table}, indent=2, default=str))
    save("exp5b_replanning_mechanism", {"environment": environment_info(), "summary": summary, "scenarios": out})


if __name__ == "__main__":
    asyncio.run(main())
