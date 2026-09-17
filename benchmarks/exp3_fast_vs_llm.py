"""Experiment 3 — Deterministic fast path vs LLM planning path.

The same requests are run through the pipeline twice:

  FAST: Listener regex → ExecutionRouter (FAST_PATH) → Executor → Overseer
  LLM : Listener LLM   → Strategist LLM (JSON plan) → Executor → Overseer

The LLM path is forced by disabling the Listener's regex classifier and
routing every intent to REASONING. Tools, executor and verifier are identical,
so the latency difference is the cost of LLM understanding + planning.
Only read-only requests are used so repeated runs have no side effects.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from common import (
    COUNTER, build_agents, disable_db_sync, environment_info, instrument_llm,
    pace, pct, reset_counter, save, stats,
)

REPS = 3
PACE_S = 6.0  # spacing between LLM-path runs (Groq free-tier TPM)
REQUESTS = [
    "what is the time",
    "check cpu usage",
    "how much memory",
    "system info",
    "list open windows",
    "show clipboard",
    "list files in documents",
    "take a screenshot",
]


async def run(tm, agents, request):
    reset_counter()
    task = await tm.submit_task(request, str(uuid.uuid4()))
    t0 = time.perf_counter()
    task = await tm.run_pipeline(task, *agents)
    ms = (time.perf_counter() - t0) * 1000
    snap = COUNTER.snapshot()
    steps = task.plan.steps if task.plan else []
    return {
        "request": request,
        # Provider throttle wait is not VoxOS latency; a 429 answered by the
        # other API key costs one extra round trip and no wait.
        "latency_ms": ms - snap["rate_limit_wait_ms"],
        "throttle_wait_ms": snap["rate_limit_wait_ms"],
        "status": task.status.value,
        "execution_class": task.execution_class,
        "intent": task.intent,
        "tools": [s.tool for s in steps],
        "arguments": [s.arguments for s in steps],
        "llm_calls": snap["calls"],
        "tokens": snap["total_tokens"],
        "llm_latency_ms": snap["llm_latency_ms"],
        "rate_limited": snap["rate_limited"],
        "errors": [s.error for s in steps if s.error] or task.error_info,
    }


async def main():
    disable_db_sync()
    instrument_llm()
    from voxos.agents.listener.agent import ListenerAgent
    from voxos.core import router as router_mod
    from voxos.core.task_manager.manager import TaskManager

    from voxos.agents.strategist.agent import StrategistAgent

    agents = build_agents()
    tm = TaskManager()
    # Read raw descriptors so staticmethod-ness survives the restore.
    original_fast = ListenerAgent.__dict__["_fast_classify"]
    original_route = router_mod.ExecutionRouter.__dict__["route"]
    original_plan = StrategistAgent.__dict__["_create_plan"]

    def use_fast():
        ListenerAgent._fast_classify = original_fast
        router_mod.ExecutionRouter.route = original_route
        StrategistAgent._create_plan = original_plan

    def use_llm():
        ListenerAgent._fast_classify = staticmethod(lambda text: None)
        router_mod.ExecutionRouter.route = lambda self, *a, **k: router_mod.ExecutionClass.REASONING

        async def llm_only(self, inp):  # skip the Strategist's own intent→tool shortcut
            return await self._llm_plan(inp)

        StrategistAgent._create_plan = llm_only

    # Warm-up (imports, first HTTP connection) — excluded from results.
    use_fast()
    await run(tm, agents, "what is the time")
    use_llm()
    await run(tm, agents, "what is the time")

    fast_rows, llm_rows = [], []
    for rep in range(REPS):
        for req in REQUESTS:
            use_fast()
            r = await run(tm, agents, req)
            r["rep"] = rep
            fast_rows.append(r)

            use_llm()
            await pace(PACE_S)
            r = await run(tm, agents, req)
            r["rep"] = rep
            llm_rows.append(r)
            f = fast_rows[-1]
            print(f"rep{rep} {req:26} fast={f['latency_ms']:8.1f}ms [{f['execution_class']},{f['status']}]  "
                  f"llm={r['latency_ms']:8.1f}ms [{r['status']}, calls={r['llm_calls']}, tok={r['tokens']}, "
                  f"429s={r['rate_limited']}]" + ("" if r["status"] == "COMPLETED" else f"  err={str(r['errors'])[:160]}")
                  + ("" if f["status"] == "COMPLETED" else f"  fast_err={str(f['errors'])[:160]}"), flush=True)

    use_fast()

    def summarise(all_rows):
        # Runs delayed by provider throttling measure the rate limiter, not VoxOS.
        rows = [r for r in all_rows if not r["throttle_wait_ms"]] or all_rows
        return {
            "runs": len(all_rows),
            "runs_excluded_rate_limited": len(all_rows) - len(rows),
            "runs_with_429": sum(bool(r["rate_limited"]) for r in all_rows),
            "latency_ms": stats([r["latency_ms"] for r in rows]),
            "p95_latency_ms": round(sorted(r["latency_ms"] for r in rows)[int(0.95 * len(rows)) - 1], 1),
            "llm_calls_per_request": round(sum(r["llm_calls"] for r in rows) / len(rows), 2),
            "tokens_per_request": round(sum(r["tokens"] for r in rows) / len(rows), 1),
            "llm_share_of_latency_pct": pct(sum(r["llm_latency_ms"] for r in rows), sum(r["latency_ms"] for r in rows)),
            "success_pct": pct(sum(r["status"] == "COMPLETED" for r in all_rows), len(all_rows)),
            "fast_path_hit_pct": pct(sum(r["execution_class"] == "FAST_PATH" for r in all_rows), len(all_rows)),
        }

    fast_s, llm_s = summarise(fast_rows), summarise(llm_rows)
    per_request = {}
    for req in REQUESTS:
        f = [r for r in fast_rows if r["request"] == req]
        l = [r for r in llm_rows if r["request"] == req]
        per_request[req] = {
            "fast_median_ms": stats([r["latency_ms"] for r in f if not r["throttle_wait_ms"]] or [r["latency_ms"] for r in f])["median"],
            "llm_median_ms": stats([r["latency_ms"] for r in l if not r["throttle_wait_ms"]] or [r["latency_ms"] for r in l])["median"],
            "fast_success": f"{sum(r['status'] == 'COMPLETED' for r in f)}/{len(f)}",
            "llm_success": f"{sum(r['status'] == 'COMPLETED' for r in l)}/{len(l)}",
            "llm_tools": sorted({t for r in l for t in r["tools"] if t}),
            "fast_tools": sorted({t for r in f for t in r["tools"] if t}),
        }
    summary = {
        "fast_path": fast_s,
        "llm_path": llm_s,
        "mean_latency_reduction_pct": pct(llm_s["latency_ms"]["mean"] - fast_s["latency_ms"]["mean"], llm_s["latency_ms"]["mean"]),
        "median_latency_reduction_pct": pct(llm_s["latency_ms"]["median"] - fast_s["latency_ms"]["median"], llm_s["latency_ms"]["median"]),
        "speedup_median_x": round(llm_s["latency_ms"]["median"] / max(fast_s["latency_ms"]["median"], 0.1), 1),
    }
    print(json.dumps({"summary": summary, "per_request": per_request}, indent=2))
    save("exp3_fast_vs_llm", {"environment": environment_info(), "summary": summary,
                              "per_request": per_request, "fast_runs": fast_rows, "llm_runs": llm_rows})


if __name__ == "__main__":
    asyncio.run(main())
