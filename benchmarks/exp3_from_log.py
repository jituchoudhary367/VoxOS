"""Rebuild Experiment 3 results from its progress log.

Used when a run had to be stopped early (both API keys at the provider's
daily token cap). Every completed run prints one line with both paths'
latencies — already net of throttle wait — so the summary can be computed
from the runs that finished.
"""

from __future__ import annotations

import json
import re
import sys

from common import environment_info, pct, save, stats

LINE = re.compile(
    r"^rep(?P<rep>\d+) (?P<request>.+?)\s+fast=\s*(?P<fast_ms>[\d.]+)ms \[(?P<fast_class>\w+),(?P<fast_status>\w+)\]\s+"
    r"llm=\s*(?P<llm_ms>[\d.]+)ms \[(?P<llm_status>\w+), calls=(?P<calls>\d+), tok=(?P<tok>\d+), 429s=(?P<r429>\d+)\]"
)


def main(log_path: str) -> None:
    fast, llm = [], []
    for line in open(log_path, encoding="utf-8"):
        m = LINE.match(line.strip())
        if not m:
            continue
        g = m.groupdict()
        fast.append({"request": g["request"].strip(), "rep": int(g["rep"]), "latency_ms": float(g["fast_ms"]),
                     "status": g["fast_status"], "execution_class": g["fast_class"], "llm_calls": 0, "tokens": 0})
        llm.append({"request": g["request"].strip(), "rep": int(g["rep"]), "latency_ms": float(g["llm_ms"]),
                    "status": g["llm_status"], "execution_class": "REASONING", "llm_calls": int(g["calls"]),
                    "tokens": int(g["tok"]), "responses_429": int(g["r429"])})

    def summarise(rows):
        lat = sorted(r["latency_ms"] for r in rows)
        return {
            "runs": len(rows),
            "runs_excluded_rate_limited": 0,
            "latency_ms": stats(lat),
            "p95_latency_ms": round(lat[max(0, int(0.95 * len(lat)) - 1)], 1),
            "llm_calls_per_request": round(sum(r["llm_calls"] for r in rows) / len(rows), 2),
            "tokens_per_request": round(sum(r["tokens"] for r in rows) / len(rows), 1),
            "success_pct": pct(sum(r["status"] == "COMPLETED" for r in rows), len(rows)),
            "fast_path_hit_pct": pct(sum(r["execution_class"] == "FAST_PATH" for r in rows), len(rows)),
        }

    f_s, l_s = summarise(fast), summarise(llm)
    per_request = {}
    for req in dict.fromkeys(r["request"] for r in fast):
        f = [r for r in fast if r["request"] == req]
        l = [r for r in llm if r["request"] == req]
        per_request[req] = {
            "fast_median_ms": stats([r["latency_ms"] for r in f])["median"],
            "llm_median_ms": stats([r["latency_ms"] for r in l])["median"],
            "fast_success": f"{sum(r['status'] == 'COMPLETED' for r in f)}/{len(f)}",
            "llm_success": f"{sum(r['status'] == 'COMPLETED' for r in l)}/{len(l)}",
        }
    summary = {
        "fast_path": f_s,
        "llm_path": l_s,
        "mean_latency_reduction_pct": pct(l_s["latency_ms"]["mean"] - f_s["latency_ms"]["mean"], l_s["latency_ms"]["mean"]),
        "median_latency_reduction_pct": pct(l_s["latency_ms"]["median"] - f_s["latency_ms"]["median"], l_s["latency_ms"]["median"]),
        "speedup_median_x": round(l_s["latency_ms"]["median"] / max(f_s["latency_ms"]["median"], 0.1), 1),
        "note": "Rebuilt from progress log; latencies exclude provider throttle wait.",
    }
    save("exp3_fast_vs_llm", {"environment": environment_info(), "summary": summary,
                              "per_request": per_request, "fast_runs": fast, "llm_runs": llm})
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/exp3_final.log")
