# VoxOS result tables


## Table V — Experimental setup

| Parameter | Value |
|---|---|
| Operating System | Windows 11 (build 10.0.26200) |
| Processor | Intel(R) Core(TM) Ultra 7 155H |
| RAM | 15.7 GB |
| Python Version | 3.13.5 |
| LLM | openai/gpt-oss-20b (Listener, Strategist); openai/gpt-oss-120b (replanning), via Groq |
| Backend | FastAPI |
| Database | PostgreSQL |
| Browser Automation | Playwright |

## Table VI — Task-completion outcomes (60 runs: 20 tasks x 3 repetitions)

| Task ID | Category | Request | Route | Steps | Success | Replanned | Time (s) |
|---|---|---|---|---|---|---|---|
| T1 | Information | What time is it? | FAST_PATH | 1 | 3/3 | 0 | 0.91 |
| T2 | Information | What operating system am I running? | FAST_PATH | 1 | 3/3 | 0 | <0.01 |
| T3 | Information | How much RAM is being used right now? | FAST_PATH | 1 | 3/3 | 0 | 0.11 |
| T4 | File system | List files in <WS> | FAST_PATH | 1 | 3/3 | 0 | <0.01 |
| T5 | File system | Find all .py files in <WS> | FAST_PATH | 1 | 3/3 | 0 | <0.01 |
| T6 | File operations | Create a folder called reports in <WS> | FAST_PATH | 1 | 3/3 | 0 | <0.01 |
| T7 | File operations | Write a file at <WS>\todo.txt with the text buy milk | REASONING | 1 | 3/3 | 3 | 2.6 |
| T8 | File operations | Read the file <WS>\notes.txt | FAST_PATH, REASONING | 1 | 3/3 | 1 | 1.4 |
| T9 | File operations | Copy <WS>\notes.txt to <WS>\notes_backup.txt | REASONING | 1 | 3/3 | 0 | 1.66 |
| T10 | File operations | Rename <WS>\report.md to final_report.md | REASONING | 1 | 3/3 | 3 | 2.49 |
| T11 | Application | Open Notepad | FAST_PATH | 1 | 3/3 | 0 | 0.59 |
| T12 | Application | What applications are currently running? | REASONING | 1 | 3/3 | 0 | 1.42 |
| T13 | Application | Close Notepad | FAST_PATH | 1 | 3/3 | 0 | 0.52 |
| T14 | Browser | Open github.com in the browser | AUTOMATION | 1 | 3/3 | 0 | 1.26 |
| T15 | Browser | Search for Python asyncio tutorial | AUTOMATION | 1 | 3/3 | 0 | 1.27 |
| T16 | Multi-step | Find the file named sample.csv in <WS> and then read its contents | REASONING | 2 | 3/3 | 0 | 1.56 |
| T17 | Multi-step | Create a folder named project in <WS>, then create a file readme.txt inside it containing Hello VoxOS, then read the file back | REASONING | 3 | 3/3 | 0 | 1.82 |
| T18 | Multi-step | Create a folder named archive in <WS> and then copy <WS>\notes.txt into that archive folder | REASONING | 2 | 3/3 | 0 | 1.94 |
| T19 | Parallel | Tell me the current time, my system information, and my RAM usage | REASONING | 3 | 3/3 | 0 | 2.01 |
| T20 | Parallel | Create two files in <WS>: a.txt containing alpha and b.txt containing beta | REASONING | 3 | 3/3 | 0 | 1.94 |

_Success is successful runs out of 3. Overall task success 100.0%, step completion 100.0% (84/84), 0 false completions. <WS> is the benchmark workspace folder._

## Table VII — Risk-Engine confusion matrix (held-out set, n=100)

|  | Predicted Safe | Predicted Dangerous |
|---|---|---|
| Actual Safe | TN = 48 | FP = 2 |
| Actual Dangerous | FN = 16 | TP = 34 |

_Command classifier. Commands unseen when the classifier was written._

## Table VIII — Risk-Engine performance (held-out set, n=100)

| Metric | Command classifier | Effective gate |
|---|---|---|
| Confusion (TP/FN/FP/TN) | 34/16/2/48 | 47/3/20/30 |
| Accuracy | 82.0% | 77.0% |
| Precision | 94.4% | 70.1% |
| Recall | 68.0% | 94.0% |
| F1 score | 79.1% | 80.3% |
| Detection rate | 68.0% | 94.0% |
| False-positive rate | 4.0% | 40.0% |
| Exact action accuracy | 76.0% | 63.0% |

_The gate is what the executor enforces: it also asks before running commands no rule recognises._

## Table IX — Deterministic fast path vs LLM planning path (21 runs per path)

| Metric | Fast path | LLM path |
|---|---|---|
| Mean latency (ms) | 115.0 | 1957.2 |
| Median latency (ms) | 2.1 | 1745.0 |
| p95 latency (ms) | 109.7 | 2766.6 |
| Std. deviation (ms) | 337.5 | 738.9 |
| LLM calls per request | 0.0 | 2.0 |
| Tokens per request | 0.0 | 2682.6 |
| Task success | 100.0% | 100.0% |

_Median latency reduction 99.9%. Latencies exclude time waiting on provider rate limits._

## Table X — Dependency-aware execution (11 topologies, 5 runs each)

| Scenario | Topology | Order kept | Concurrent pairs | Failure handling | Wall (s) | Speedup |
|---|---|---|---|---|---|---|
| S1 Chain A→B→C | A→B→C | 5/5 | n/a | 5/5 | 0.9 | 0.97x |
| S2 Fan-in A,B→C | A→C, B→C | 5/5 | 5/5 | 5/5 | 0.6 | 1.46x |
| S3 Independent A,B,C | A  B  C | 5/5 | 15/15 | 5/5 | 0.3 | 2.91x |
| S4 Diamond | A→B, A→C, B→D, C→D | 5/5 | 5/5 | 5/5 | 0.9 | 1.29x |
| S5 Uneven fan-in | A(0.2s)→C, B(0.6s)→C | 5/5 | 5/5 | 5/5 | 0.9 | 1.2x |
| S6 Wide parallel ×8 | 8 independent | 5/5 | 140/140 | 5/5 | 0.3 | 7.76x |
| F1 Chain, middle fails | A→B✗→C | 5/5 | n/a | 5/5 | 0.6 | 0.97x |
| F2 Fan-in, one branch fails | A✗→C, B→C | 5/5 | 5/5 | 5/5 | 0.3 | 1.93x |
| F3 Independent, one fails | A✗  B  C | 5/5 | 15/15 | 5/5 | 0.3 | 2.92x |
| F4 Transitive skip | A✗→B→C→D | 5/5 | n/a | 5/5 | 0.3 | 0.97x |
| F5 Diamond, one arm fails | A→B✗, A→C, B→D, C→D | 5/5 | 5/5 | 5/5 | 0.6 | 1.45x |

_Dependency order respected 100.0%, independent steps concurrent 100.0%, failure propagation correct 100.0% over 55 runs._

## Table XI — Failure recovery with fault injection (2 runs per scenario)

| ID | Scenario | Control | Recovered | Failed | Replans | Alt. plan | Added latency (ms) |
|---|---|---|---|---|---|---|---|
| R1 | Transient write fault (1×) | 2/2 | 2 | 0 | 1.0 | 1 | 1076.4 |
| R2 | Transient write fault (2×) | 2/2 | 2 | 0 | 1.5 | 2 | 1524.7 |
| R3 | Transient folder fault in multi-step plan | 2/2 | 2 | 0 | 1.0 | 1 | - |
| R4 | Transient read fault | 2/2 | 2 | 0 | 2.0 | 0 | 396.2 |
| R5 | Persistent write fault (tool unusable) | 2/2 | 2 | 0 | 2.0 | 2 | 2448.8 |
| R6 | Non-retryable: file does not exist | n/a | 0 | 2 | 0.0 | 0 | - |

_Recovery rate 100.0% (10/10 fault runs); mean 1.5 replans; R6 is a non-retryable error and correctly failed without replanning._
