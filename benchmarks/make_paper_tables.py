"""Write the result tables as CSV and IEEE-style LaTeX for pasting into the paper.

CSV: paste into Word (Insert > Table > Convert Text to Table, comma separated)
     or into Excel/Google Sheets directly.
LaTeX: paste a \\begin{table} block into an IEEEtran document as-is.

    python make_paper_tables.py     ->  benchmarks/paper/
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

RES = Path(__file__).parent / "results"
OUT = Path(__file__).parent / "paper"


def load(name):
    p = RES / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def tex_escape(text: str) -> str:
    text = str(text)
    for a, b in [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
                 ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
                 ("<", r"\textless{}"), (">", r"\textgreater{}"), ("×", r"$\times$"),
                 ("→", r"$\rightarrow$"), ("—", "--"), ("µ", r"$\mu$")]:
        text = text.replace(a, b)
    return text


class Table:
    def __init__(self, key: str, number: str, caption: str, headers: list[str], rows: list[list],
                 align: str | None = None, note: str = "") -> None:
        self.key, self.number, self.caption = key, number, caption
        self.headers, self.rows = headers, rows
        self.align = align or ("l" + "c" * (len(headers) - 1))
        self.note = note

    def write_csv(self) -> None:
        with (OUT / f"{self.key}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(self.headers)
            writer.writerows(self.rows)

    def to_tex(self) -> str:
        lines = [
            r"\begin{table}[htbp]",
            r"\caption{" + tex_escape(self.caption) + "}",
            r"\label{tab:" + self.key + "}",
            r"\centering",
            r"\begin{tabular}{" + self.align + "}",
            r"\hline",
            " & ".join(r"\textbf{" + tex_escape(h) + "}" for h in self.headers) + r" \\",
            r"\hline",
        ]
        lines += [" & ".join(tex_escape(c) for c in row) + r" \\" for row in self.rows]
        lines += [r"\hline", r"\end{tabular}"]
        if self.note:
            lines.append(r"\vspace{2pt}\footnotesize " + tex_escape(self.note))
        lines.append(r"\end{table}")
        return "\n".join(lines)


def build() -> list[Table]:
    e1, e2, e3 = load("exp1_task_completion"), load("exp2_security"), load("exp3_fast_vs_llm")
    e4, e5, e5b = load("exp4_dag_execution"), load("exp5_failure_recovery"), load("exp5b_replanning_mechanism")
    tables: list[Table] = []

    env = next((e["environment"] for e in (e1, e2, e3, e4, e5, e5b) if e), None)
    if env:
        tables.append(Table(
            "tableV_setup", "V", "Experimental setup",
            ["Parameter", "Value"],
            [["Operating System", env["os"]],
             ["Processor", env["processor"]],
             ["RAM", f"{env['ram_gb']} GB"],
             ["Python Version", env["python"]],
             ["LLM", f"{env['llm_fast_model']} (Listener, Strategist); {env['llm_strong_model']} (replanning), via Groq"],
             ["Backend", "FastAPI"],
             ["Database", "PostgreSQL"],
             ["Browser Automation", "Playwright"]],
            align="ll"))

    if e1:
        s = e1["summary"]
        tables.append(Table(
            "tableVI_tasks", "VI",
            f"Task-completion outcomes ({s['runs']} runs: 20 tasks x {s.get('reps', 1)} repetitions)",
            ["Task ID", "Category", "Request", "Route", "Steps", "Success", "Replanned", "Time (s)"],
            [[t["id"], t["category"], t["request"], ", ".join(t["routes"]), t["steps_planned_median"],
              t["success"], t["replanned_runs"],
              t["mean_time_s"] if t["mean_time_s"] >= 0.01 else "<0.01"] for t in e1["per_task"]],
            align="llp{3.2cm}lcccc",
            note=f"Success is successful runs out of {s.get('reps', 1)}. Overall task success "
                 f"{s['task_success_rate_pct']}%, step completion {s['step_completion_rate_pct']}% "
                 f"({s['steps_completed']}/{s['steps_planned']}), {s['false_completions']} false completions. "
                 f"<WS> is the benchmark workspace folder."))

    if e2 and "heldout" in e2:
        c = e2["heldout"]["command_classifier"]
        g = e2["heldout"]["effective_gate_os_shell_command"]
        cm, gm = c["confusion_matrix"], g["confusion_matrix"]
        tables.append(Table(
            "tableVII_confusion", "VII",
            "Risk-Engine confusion matrix (held-out set, n=100)",
            ["", "Predicted Safe", "Predicted Dangerous"],
            [["Actual Safe", f"TN = {cm['TN']}", f"FP = {cm['FP']}"],
             ["Actual Dangerous", f"FN = {cm['FN']}", f"TP = {cm['TP']}"]],
            align="lcc",
            note="Command classifier. Commands unseen when the classifier was written."))
        tables.append(Table(
            "tableVIIb_metrics", "VIII",
            "Risk-Engine performance (held-out set, n=100)",
            ["Metric", "Command classifier", "Effective gate"],
            [["Confusion (TP/FN/FP/TN)", f"{cm['TP']}/{cm['FN']}/{cm['FP']}/{cm['TN']}",
              f"{gm['TP']}/{gm['FN']}/{gm['FP']}/{gm['TN']}"]] +
            [[label, f"{c[k]}%", f"{g[k]}%"] for label, k in [
                ("Accuracy", "accuracy_pct"), ("Precision", "precision_pct"), ("Recall", "recall_pct"),
                ("F1 score", "f1_pct"), ("Detection rate", "detection_rate_pct"),
                ("False-positive rate", "false_positive_rate_pct"),
                ("Exact action accuracy", "exact_action_accuracy_pct")]],
            align="lcc",
            note="The gate is what the executor enforces: it also asks before running commands no rule recognises."))

    if e3:
        s = e3["summary"]
        f, l = s["fast_path"], s["llm_path"]
        tables.append(Table(
            "tableIX_fastpath", "IX",
            f"Deterministic fast path vs LLM planning path ({f['runs']} runs per path)",
            ["Metric", "Fast path", "LLM path"],
            [["Mean latency (ms)", f["latency_ms"]["mean"], l["latency_ms"]["mean"]],
             ["Median latency (ms)", f["latency_ms"]["median"], l["latency_ms"]["median"]],
             ["p95 latency (ms)", f["p95_latency_ms"], l["p95_latency_ms"]],
             ["Std. deviation (ms)", f["latency_ms"]["stdev"], l["latency_ms"]["stdev"]],
             ["LLM calls per request", f["llm_calls_per_request"], l["llm_calls_per_request"]],
             ["Tokens per request", f["tokens_per_request"], l["tokens_per_request"]],
             ["Task success", f"{f['success_pct']}%", f"{l['success_pct']}%"]],
            align="lcc",
            note=f"Median latency reduction {s['median_latency_reduction_pct']}%. "
                 f"Latencies exclude time waiting on provider rate limits."))

    if e4:
        s = e4["summary"]
        tables.append(Table(
            "tableX_dag", "X",
            "Dependency-aware execution (11 topologies, 5 runs each)",
            ["Scenario", "Topology", "Order kept", "Concurrent pairs", "Failure handling", "Wall (s)", "Speedup"],
            [[r["scenario"], r["topology"], r["deps_respected"], r["concurrency"], r["failure_handling"],
              r["wall_s"]["mean"], f"{r['speedup_mean']}x"] for r in e4["scenarios"]],
            align="llccccc",
            note=f"Dependency order respected {s['dependency_order_respected_pct']}%, independent steps concurrent "
                 f"{s['independent_pairs_run_concurrently_pct']}%, failure propagation correct "
                 f"{s['failure_propagation_correct_pct']}% over {s['runs']} runs."))

    if e5:
        s = e5["summary"]
        tables.append(Table(
            "tableXI_recovery", "XI",
            "Failure recovery with fault injection (2 runs per scenario)",
            ["ID", "Scenario", "Control", "Recovered", "Failed", "Replans", "Alt. plan", "Added latency (ms)"],
            [[r["id"], r["scenario"], r["control_success"], r["recovery_success"], r["recovery_failure"],
              r["mean_replans"], r["alternative_plans"],
              "-" if r["added_latency_ms"] is None else r["added_latency_ms"]] for r in e5["scenarios"]],
            align="llcccccc",
            note=f"Recovery rate {s['recovery_rate_pct']}% ({s['recovery_success']}/{s['fault_runs']} fault runs); "
                 f"mean {s['mean_replans_when_recovered']} replans; R6 is a non-retryable error and correctly "
                 f"failed without replanning."))

    return tables


def main() -> None:
    OUT.mkdir(exist_ok=True)
    tables = build()
    tex_parts, md_parts = [], ["# VoxOS result tables\n"]
    for t in tables:
        t.write_csv()
        tex_parts.append(t.to_tex())
        md_parts.append(f"\n## Table {t.number} — {t.caption}\n")
        md_parts.append("| " + " | ".join(t.headers) + " |")
        md_parts.append("|" + "---|" * len(t.headers))
        md_parts += ["| " + " | ".join(str(c) for c in row) + " |" for row in t.rows]
        if t.note:
            md_parts.append(f"\n_{t.note}_")
    (OUT / "all_tables.tex").write_text("\n\n".join(tex_parts) + "\n", encoding="utf-8")
    (OUT / "all_tables.md").write_text("\n".join(md_parts) + "\n", encoding="utf-8")
    for t in tables:
        print(f"Table {t.number:4} {t.caption[:58]:60} -> paper/{t.key}.csv")
    print(f"\nAlso: paper/all_tables.tex (LaTeX), paper/all_tables.md (Markdown)")


if __name__ == "__main__":
    main()
