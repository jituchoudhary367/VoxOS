## Methodology and threats to validity

### Harness

All experiments use the real agents, tool registry, risk engine and executor,
with the Groq LLM configured in `.env`. The harness only adds instrumentation:

- **Latency scope.** Database sync to PostgreSQL is disabled during runs, so
  latency measures the agents rather than remote database writes.
- **Sandbox.** File tasks run in a sandbox workspace (`~/VoxBench`) that is
  reseeded before every repetition.
- **Approvals.** An auto-approver stands in for the human on HIGH-risk
  approvals. The executor polls for approval once per second, which adds
  about 1 s to each approved step.
- **Rate limits.** Groq's free tier allows 8,000 tokens per minute. When a
  call gets a 429, the harness waits out `retry-after` and records the wait,
  and that wait is subtracted from every reported latency. Without this, the
  system's own fallback (a local Ollama that is not installed) turns a
  throttle into a task failure. That is a real deployment risk, but it is not
  agent behaviour.
- **Sample sizes.** Exp. 1: 20 tasks × 3 repetitions (60 runs). Exp. 2: 100
  development commands and 100 held-out commands. Exp. 3: 8 requests × 3
  repetitions per path, planned as 24 runs per path. The last 3 runs were
  stopped once both API keys reached the daily token cap, so 21 runs per path
  were analysed (rebuilt from the progress log with `exp3_from_log.py`;
  latencies exclude throttle wait). Exp. 4: 11 topologies × 5 (55 runs).
  Exp. 5: 6 scenarios × 2 (10 fault runs plus 10 no-fault controls).
  Exp. 5b: 6 × 5.
- **Success criterion.** Exp. 1 counts a task as successful only when the
  pipeline reports COMPLETED **and** an independent check of the resulting OS
  state passes. Exp. 3 uses pipeline status only. For "Close Notepad", the
  check waits up to 3 s for the process to exit, because Windows 11 Notepad's
  process outlives its window by about a second.
- **Two API keys.** Groq's free tier also caps each organisation at 200,000
  tokens per day. The final runs used two keys from separate organisations,
  switching to the other key on a 429. The model and endpoint are the same
  for both, so measurements are unaffected.

### Defects found by the first benchmark run, and fixed

The first run on the unmodified system scored 30% task success (6/20), with
0% LLM-driven recovery. These defects caused that, and all of them are now
fixed, each with a regression test in `tests/unit/test_agent_routing_fixes.py`
or `tests/security/test_risk_gate_fixes.py`:

| # | Defect | Fix |
|---|---|---|
| D0 | Listener `max_tokens=256`, and gpt-oss reasoning ran out of budget, so Groq rejected every LLM classification with `json_validate_failed` | `max_tokens` raised to 1024; `reasoning_effort="low"` for JSON-mode gpt-oss calls |
| D1 | The Strategist saw tool names only, so it invented argument keys (`path` instead of `file_path`) | The prompt now lists every tool's exact argument signature (`describe_tools`) |
| D2 | An LLM-guessed fast-path intent sent its free-form entities straight to the tool and skipped planning | An LLM intent keeps the fast path only if its entities validate against the tool schema and the request is a single action |
| D3 | The system-info regex matched "os" inside any word ("Cl**os**e Notepad") | Word boundaries added |
| D4 | The rule classifier stripped all punctuation, breaking Windows paths and domains | Paths and URLs are captured from the original text; domains route to the browser |
| D5 | `VALIDATION_ERROR` was fatal, and failed deterministic routes never replanned | Planner errors now trigger replanning; a failed FAST_PATH/AUTOMATION run escalates to the Strategist |
| D6 | 11 desktop tools were missing from the permission map, so they were always denied | All registered tools are mapped, enforced by a test |
| D7 | The shell tool required approval for every command (gate FPR 100%) | The gate follows the command classifier: LOW/MEDIUM run, HIGH or unrecognised commands ask, CRITICAL is blocked |
| D8 | The Strategist's own intent→tool shortcut collapsed compound requests into one step | The shortcut is skipped for compound requests |
| D9 | Plan steps could not use earlier steps' outputs, so "find a file, then read it" had to guess the path at planning time | Steps can reference `{{step_N.path}}`; the Executor fills in the real value before validation, risk checks and execution |

### Risk classifier: development vs held-out protocol

The first Exp. 2 dataset exposed classifier gaps. Fixing those gaps against
the same dataset would inflate its score, so:

1. A new 100-command **held-out set** (`security_heldout.py`, md5
   `57c609db…`) was written and labelled *before* any classifier change.
2. Classifier patterns were added only for the failure classes seen in the
   development set, generalised: PowerShell destructive verb families,
   split `rm -r -f` flags, recursive deletion of root or protected paths,
   `mkfs`, fork bombs, `find / -delete`, `chmod -R` on `/`, shadow-copy
   deletion, `net user` account changes, decode-then-execute, `iex` and
   download cradles, quote-splicing (`r''m`), and `certutil -decode`. Two
   false-positive fixes were also made: text printed by `echo` is treated as
   data, and read-only commands on protected paths are allowed.
3. The held-out set was never used to write or tune rules.

**Report the held-out numbers.** The development-set numbers after the fixes
are optimistic by construction. Remaining held-out misses (LOLBins such as
`mshta`/`regsvr32`/`bitsadmin`, `bcdedit`, firewall and Defender tampering,
`$IFS` obfuscation, process substitution) are genuine gaps in a rule-based
approach. Fixing them would need a fresh held-out set to measure honestly.

### Remaining limitations

- Exp. 3 forces the LLM path for requests the rule classifier handles, which
  isolates the cost of LLM understanding and planning; it does not describe
  normal routing.
- Exp. 5b uses a scripted planner to test the replanning mechanism apart from
  LLM plan quality. Exp. 5 is the end-to-end result.
- Results come from one machine, one LLM provider on its free tier, and
  English requests only.
- A ReAct baseline, which the draft paper mentions, was not implemented.
