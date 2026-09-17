"""Render benchmarks/results/*.json into paper-ready Markdown tables."""

from __future__ import annotations

import json
from pathlib import Path

RES = Path(__file__).parent / "results"


def load(name):
    p = RES / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def code(cmd: str) -> str:
    return "`" + cmd.strip().replace("|", "\\|") + "`"


def dash(value):
    return "—" if value is None else value


def section_setup(parts, env):
    parts.append("## Table V — Experimental setup\n")
    parts.append(md_table(["Parameter", "Value"], [
        ["Operating System", env["os"]],
        ["Processor", env["processor"]],
        ["RAM", f"{env['ram_gb']} GB"],
        ["Python Version", env["python"]],
        ["LLM", f"{env['llm_fast_model']} (Listener, Strategist); {env['llm_strong_model']} (replanning); via Groq API"],
        ["Backend", "FastAPI"],
        ["Database", "PostgreSQL"],
        ["Browser Automation", "Playwright"],
    ]))


def section_tasks(parts, e1):
    s = e1["summary"]
    reps = s.get("reps", 1)
    parts.append(f"\n## Table VI — Task-completion outcomes ({s['runs']} runs: 20 tasks × {reps})\n")
    parts.append(md_table(
        ["Task ID", "Category", "Route", "Steps", "Outcome (success/runs)", "Replanned (runs)", "Mean time (s)"],
        [[t["id"], t["category"], ", ".join(t["routes"]), t["steps_planned_median"], t["success"],
          t["replanned_runs"], t["mean_time_s"] if t["mean_time_s"] >= 0.01 else "<0.01"] for t in e1["per_task"]]))
    parts.append(
        f"\nTask success rate **{s['task_success_rate_pct']}%** ({s['successful_tasks']}/{s['runs']} runs); "
        f"step completion rate **{s['step_completion_rate_pct']}%** ({s['steps_completed']}/{s['steps_planned']} steps); "
        f"replanning in {s['tasks_replanned']} runs ({s['replanning_rate_pct']}%); "
        f"false completions {s['false_completions']}; mean time {s['mean_time_s']} s "
        f"(provider throttling wait excluded); simple tasks {s['simple_success_pct']}% vs complex "
        f"{s['complex_success_pct']}%; {s['tasks_successful_all_reps']}/20 tasks succeeded in every run.\n")
    parts.append(md_table(["Category", "Success (runs)"], sorted(s["by_category"].items())))
    parts.append("\nRequests (`<WS>` = benchmark workspace folder):\n")
    parts.append(md_table(["Task ID", "Request"], [[t["id"], t["request"]] for t in e1["per_task"]]))


def section_security(parts, e2):
    sets = [
        ("heldout", "held-out set — report these",
         "100 unseen commands, labelled before the classifier fixes were written. Unbiased estimate."),
        ("development", "development set — for reference only",
         "The classifier fixes were derived from this set's failures, so these numbers are optimistic by construction."),
    ]
    for key, title, note in sets:
        if key not in e2:
            continue
        d = e2[key]
        c, g = d["command_classifier"], d["effective_gate_os_shell_command"]
        cm, gm = c["confusion_matrix"], g["confusion_matrix"]
        parts.append(f"\n## Table VII — Risk-Engine confusion matrix ({title})\n")
        parts.append(f"_{note}_\n")
        parts.append("**Command classifier**\n")
        parts.append(md_table(["", "Predicted Safe", "Predicted Dangerous"], [
            ["Actual Safe", f"TN = {cm['TN']}", f"FP = {cm['FP']}"],
            ["Actual Dangerous", f"FN = {cm['FN']}", f"TP = {cm['TP']}"],
        ]))
        parts.append("\n**Effective gate (what the executor enforces for `os.shell_command`)**\n")
        parts.append(md_table(["", "Predicted Safe", "Predicted Dangerous"], [
            ["Actual Safe", f"TN = {gm['TN']}", f"FP = {gm['FP']}"],
            ["Actual Dangerous", f"FN = {gm['FN']}", f"TP = {gm['TP']}"],
        ]))
        parts.append("\n" + md_table(["Metric", "Command classifier", "Effective gate"], [
            [label, f"{c[k]}%", f"{g[k]}%"] for label, k in [
                ("Accuracy", "accuracy_pct"), ("Precision", "precision_pct"), ("Recall", "recall_pct"),
                ("F1", "f1_pct"), ("Detection rate", "detection_rate_pct"),
                ("False-positive rate", "false_positive_rate_pct"),
                ("Exact action accuracy (allow / approve / block)", "exact_action_accuracy_pct"),
                ("Must-block commands actually blocked", "block_rate_on_must_block_pct"),
            ]]))
        parts.append("\n**Action outcomes**\n")
        outcomes = sorted(set(c["outcomes"]) | set(g["outcomes"]))
        parts.append(md_table(["Outcome", "Classifier", "Gate"],
                              [[o, c["outcomes"].get(o, 0), g["outcomes"].get(o, 0)] for o in outcomes]))
        parts.append("\n**Per category**\n")
        parts.append(md_table(
            ["Category", "n", "Classifier detection %", "Classifier exact action %", "Gate detection %", "Gate exact action %"],
            [[k, v["n"], v["detection_pct"], v["exact_action_pct"],
              g["per_category"][k]["detection_pct"], g["per_category"][k]["exact_action_pct"]]
             for k, v in c["per_category"].items()]))
        if d["misclassified_by_classifier"]:
            parts.append("\n**Commands the classifier got wrong**\n")
            parts.append(md_table(["Category", "Command", "Expected", "Classifier", "Gate"],
                                  [[m["category"], code(m["command"]), m["expected"], m["classifier_action"], m["gate_action"]]
                                   for m in d["misclassified_by_classifier"]]))
        lat = d["classification_latency_us"]
        parts.append(f"\nClassification latency: mean {lat['mean']} µs, median {lat['median']} µs, p95 {lat['p95']} µs.")


def section_fast_vs_llm(parts, e3):
    s = e3["summary"]
    f, l = s["fast_path"], s["llm_path"]
    parts.append(f"\n## Table VIII — Fast path vs LLM planning path ({f['runs']} runs per path; 8 requests × up to 3)\n")
    parts.append(md_table(["Metric", "Fast path", "LLM path"], [
        ["Runs", f["runs"], l["runs"]],
        ["Mean latency (ms)", f["latency_ms"]["mean"], l["latency_ms"]["mean"]],
        ["Median latency (ms)", f["latency_ms"]["median"], l["latency_ms"]["median"]],
        ["p95 latency (ms)", f["p95_latency_ms"], l["p95_latency_ms"]],
        ["Std dev (ms)", f["latency_ms"]["stdev"], l["latency_ms"]["stdev"]],
        ["LLM calls / request", f["llm_calls_per_request"], l["llm_calls_per_request"]],
        ["Tokens / request", f["tokens_per_request"], l["tokens_per_request"]],
        ["Task success", f"{f['success_pct']}%", f"{l['success_pct']}%"],
    ]))
    parts.append(f"\nMedian latency reduction **{s['median_latency_reduction_pct']}%** "
                 f"(mean {s['mean_latency_reduction_pct']}%; {s['speedup_median_x']}× faster at the median). "
                 f"Latencies exclude time spent waiting on provider rate limits.\n")
    parts.append(md_table(["Request", "Fast median (ms)", "LLM median (ms)", "Fast success", "LLM success"],
                          [[k, v["fast_median_ms"], v["llm_median_ms"], v["fast_success"], v["llm_success"]]
                           for k, v in e3["per_request"].items()]))


def section_dag(parts, e4):
    parts.append("\n## Table IX — Dependency-aware execution (11 topologies × 5 runs each)\n")
    parts.append(md_table(["Scenario", "Topology", "Deps respected", "Concurrent pairs", "Failure handling",
                           "Wall (s)", "Sequential (s)", "Speedup"],
                          [[r["scenario"], r["topology"], r["deps_respected"], r["concurrency"],
                            r["failure_handling"], r["wall_s"]["mean"], r["sequential_s"], f"{r['speedup_mean']}×"]
                           for r in e4["scenarios"]]))
    s = e4["summary"]
    parts.append(f"\nDependency order respected {s['dependency_order_respected_pct']}%; independent steps ran "
                 f"concurrently {s['independent_pairs_run_concurrently_pct']}%; failure propagation correct "
                 f"{s['failure_propagation_correct_pct']}% ({s['runs']} runs).")


def section_recovery(parts, e5, title, note):
    parts.append(f"\n## {title}\n")
    parts.append(f"_{note}_\n")
    parts.append(md_table(["ID", "Scenario", "Control OK", "Initial success", "Recovered", "Recovery failed",
                           "Mean replans", "Alt. plans", "Added latency (ms)"],
                          [[r["id"], r["scenario"], r["control_success"], r["initial_success"],
                            r["recovery_success"], r["recovery_failure"], r["mean_replans"],
                            r["alternative_plans"], dash(r["added_latency_ms"])]
                           for r in e5["scenarios"]]))
    s = e5["summary"]
    fail_fast = s.get("non_retryable_fail_fast_correct") or s.get("non_retryable_fail_fast")
    parts.append(f"\nRecovered {s['recovery_success']}/{s['fault_runs']} fault runs ({s['recovery_rate_pct']}%); "
                 f"recovery failures {s['recovery_failure']}; mean replans when recovered "
                 f"{s['mean_replans_when_recovered']}; mean added latency {dash(s['mean_added_latency_ms'])} ms; "
                 f"non-retryable error failed fast without replanning in {fail_fast} runs.")


def section_glance(parts, e1, e2, e3, e4, e5):
    rows = []
    if e1:
        s = e1["summary"]
        rows += [["Task success rate", f"{s['task_success_rate_pct']}%", f"{s['successful_tasks']}/{s['runs']} runs"],
                 ["Step completion rate", f"{s['step_completion_rate_pct']}%", f"{s['steps_completed']}/{s['steps_planned']} steps"],
                 ["Replanning rate", f"{s['replanning_rate_pct']}%", f"{s['tasks_replanned']} runs"]]
    if e2 and "heldout" in e2:
        c = e2["heldout"]["command_classifier"]
        g = e2["heldout"]["effective_gate_os_shell_command"]
        rows += [["Risk classifier accuracy / precision / recall / F1 (held-out)",
                  f"{c['accuracy_pct']} / {c['precision_pct']} / {c['recall_pct']} / {c['f1_pct']}%",
                  f"FPR {c['false_positive_rate_pct']}%"],
                 ["Effective gate detection rate / FPR (held-out)",
                  f"{g['detection_rate_pct']}% / {g['false_positive_rate_pct']}%", "approval or block counts as detected"]]
    if e3:
        s = e3["summary"]
        rows += [["Fast-path median latency reduction", f"{s['median_latency_reduction_pct']}%",
                  f"{s['fast_path']['latency_ms']['median']} ms vs {s['llm_path']['latency_ms']['median']} ms"]]
    if e4:
        s = e4["summary"]
        rows += [["DAG executor correctness", f"{s['dependency_order_respected_pct']}%",
                  f"order, concurrency and failure propagation over {s['runs']} runs"]]
    if e5:
        s = e5["summary"]
        rows += [["Failure recovery rate (LLM replanning)", f"{s['recovery_rate_pct']}%",
                  f"{s['recovery_success']}/{s['fault_runs']} fault-injected runs"]]
    if rows:
        parts.append("\n## At a glance\n")
        parts.append(md_table(["Metric", "Value", "Basis"], rows))


def main():
    e1 = load("exp1_task_completion")
    e2 = load("exp2_security")
    e3 = load("exp3_fast_vs_llm")
    e4 = load("exp4_dag_execution")
    e5 = load("exp5_failure_recovery")
    e5b = load("exp5b_replanning_mechanism")

    parts = ["# VoxOS — Experimental Results\n"]
    section_glance(parts, e1, e2, e3, e4, e5)
    env = next((e["environment"] for e in (e1, e2, e3, e4, e5, e5b) if e), None)
    if env:
        parts.append("")
        section_setup(parts, env)
    if e1:
        section_tasks(parts, e1)
    if e2:
        section_security(parts, e2)
    if e3:
        section_fast_vs_llm(parts, e3)
    if e4:
        section_dag(parts, e4)
    if e5:
        section_recovery(parts, e5, "Table X — Failure recovery with the LLM Strategist (2 runs each)",
                         "Faults injected into real tools; the Strategist (LLM) replans from the captured failure.")
    if e5b:
        section_recovery(parts, e5b, "Table XI — Replanning loop in isolation (scripted planner, 5 runs each)",
                         "Same faults, deterministic planner: measures the mechanism, not LLM plan quality. "
                         "Added latency is orchestration only.")

    notes = Path(__file__).parent / "METHODOLOGY.md"
    if notes.exists():
        parts.append("\n" + notes.read_text(encoding="utf-8"))

    out = Path(__file__).parent / "RESULTS.md"
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
