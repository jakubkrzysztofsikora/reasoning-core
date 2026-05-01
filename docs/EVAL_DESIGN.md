# Evaluation Design: reasoning-core Hook vs. Vanilla Claude Code

**Benchmark**: SWE-bench Verified (stratified subsample)  
**Arms**: Treatment = Claude Opus 4.7 + PreToolUse hook + S2 sidecar running; Control = Claude Opus 4.7 with no hooks, no sidecar  
**Date authored**: 2026-05-01

---

## 1. Hypothesis

### H1 (Primary)
The treatment arm (PreToolUse hook + S2 sidecar) reduces the **regression rate** — the fraction of submitted patches that break at least one previously-passing test in the target repository — by ≥ 15 percentage points relative to the control arm (resolved_rate ± regression) on a stratified 100-task subsample of SWE-bench Verified, with two-sided Wilcoxon signed-rank p < 0.05.

Formally: let `R_t` = regression rate for treatment, `R_c` = regression rate for control on matched task pairs.  
H1: `R_c − R_t ≥ 0.15`

### H0 (Null)
`R_c − R_t = 0` (the hook has no effect on regression rate).

### Why this matters
The entire point of reasoning-core is to block architectural regressions before they reach the filesystem; measuring whether it actually does that on real open-source bugs is the only honest test of the system's value proposition.

---

## 2. Task Set

### Primary benchmark: SWE-bench Verified

**Justification.** SWE-bench Verified is the only public benchmark that supplies a real repository, a real failing test, and a real ground-truth patch for each task — exactly what is needed to measure regression rate on pre-existing tests independently of whether Claude's patch is "correct". LiveCodeBench and BigCodeBench are synthesis benchmarks; they produce no pre-existing test suites against which to measure regression rate. Aider polyglot is multilingual but its tasks are often small and do not expose architectural coupling.

### Inclusion / exclusion criteria

| Criterion | Rule |
|-----------|------|
| Language | Python only (`.py` files; Tree-sitter Python grammar required) |
| Estimated human time | ≤ 30 minutes (SWE-bench `difficulty` field: `easy` or `medium`) |
| Docker requirement | Excluded: tasks whose repo requires Docker to install deps |
| Repo age | Included: repos with ≥ 50 tests in their test suite at the task's commit SHA |
| File count touched by gold patch | ≤ 5 files (keeps MultiEdit vs. Edit distribution tractable) |
| Language in gold patch | Excluded if gold patch touches `.c`, `.rs`, `.go` (unsupported by the sidecar; hook would fail-open and add no signal) |

### Sample size and power calculation

**Target**: n = 100 matched task pairs (200 runs total, 100 treatment + 100 control, each pair sharing a task ID).

**Power calculation** (one-sided sign/Wilcoxon proxy via normal approximation):

```
α = 0.05 (two-sided → z_{α/2} = 1.96)
β = 0.20  (power = 0.80 → z_β = 0.842)
Minimum detectable effect (MDE) on regression rate: δ = 0.15
Assumed control regression rate: p_c = 0.35 (conservative estimate from SWE-bench Verified baselines)
Assumed treatment regression rate: p_t = 0.20
Variance under H0 (paired sign test): σ² ≈ p_c(1−p_c)
Cohen's h = 2·arcsin(√p_c) − 2·arcsin(√p_t) ≈ 0.34
Required n ≈ (z_{α/2} + z_β)² / h² = (1.96 + 0.842)² / 0.34² ≈ (7.86) / 0.116 ≈ 68
```

n = 100 provides power ≈ 0.87, adding ~20% headroom above the minimum for expected timeouts and errors (which are not dropped; see Section 5).

**Stratification**: sample uniformly from 3 strata by number of test-suite tests touched by the gold patch: [1], [2–5], [6+], ~34 tasks each. This ensures the sample is not dominated by single-test fixes.

**Task list file**: `/Users/jakubsikora/Repos/personal/reasoning-core/eval/task_list.txt`  
Format: one `{repo}_{issue_number}` per line, matching SWE-bench Verified task IDs.

---

## 3. Metrics

### 3.1 Primary metric

**Regression Rate (RR)**  
Fraction of tasks where the submitted patch causes at least one previously-passing test to fail in the target repository's full test suite.  
Computed from: `eval/results/{task_id}/{arm}/test_results.json` — field `newly_failing_tests` (list of test IDs that passed on `base_commit` but fail on the patched tree). `RR = count(tasks where len(newly_failing_tests) > 0) / n`.

### 3.2 Secondary metrics

**Resolved Rate (ResR)**  
Fraction of tasks where all gold-patch test assertions pass after Claude's patch is applied.  
Computed from: `eval/results/{task_id}/{arm}/test_results.json` — field `gold_tests_passing` (bool).  
`ResR = count(gold_tests_passing == true) / n`.

**Code-Quality Drift: AST Edit Distance (AED)**  
Number of AST node insertions + deletions between the gold patch and Claude's patch, computed with `ast.dump` diff on the submitted file(s).  
Computed by: `eval/scripts/ast_edit_distance.py <gold_patch_path> <claude_patch_path>` — writes `ast_edit_distance` (int) to `per_task_metrics.json`.

**Code-Quality Drift: Cyclomatic Delta (CycΔ)**  
Difference in McCabe cyclomatic complexity (sum over all functions in touched files) between the patched tree and the base commit tree. Positive = added complexity.  
Computed by: `eval/scripts/cyclomatic_delta.py <base_dir> <patched_dir> <files_touched>` — writes `cyclomatic_delta` (float) to `per_task_metrics.json`.

**Code-Quality Drift: Fan-in/Fan-out Delta (FIOΔ)**  
Change in max fan-in and max fan-out in the call graph of touched modules, using `src.s2_core.build_call_graph`.  
Computed by: calling `score_change` on the base vs. patched file and reading `risk_vector[fan_in]` and `risk_vector[fan_out]` from the returned `ImpactReport`. Written to `per_task_metrics.json`.

**Hook False-Positive Block Rate (FPBR)** — treatment arm only  
Fraction of tool calls blocked (hook exit 2) on tasks where Claude's final submitted patch is correct (i.e., `gold_tests_passing == true` AND `newly_failing_tests == []`).  
Computed from: `eval/results/{task_id}/treatment/hook_events.jsonl` — count events with `exit_code == 2` grouped by outcome label. `FPBR = blocked_correct / total_tool_calls_on_correct_tasks`.

**Hook True-Positive Block Rate (TPBR)** — treatment arm only  
Fraction of tool calls blocked on tasks where the blocked edit would have introduced a regression (determined by applying the blocked `after_src` to disk and running the test suite in a sandboxed checkout). This requires a secondary pass: `eval/scripts/replay_blocked_edit.sh <task_id>` applies the blocked content to a scratch clone and runs `pytest` with a 60s timeout. Written to `hook_events.jsonl` as `replay_result`.  
`TPBR = blocked_regressive / total_blocked_tool_calls`.

**Block-Recovery Rate (BRR)** — treatment arm only  
Fraction of blocked tool calls followed by a successful retry (next tool call on the same file is not blocked and the task eventually resolves).  
Computed from `hook_events.jsonl`: for each block event, look ahead in the event sequence for the same `file_path`. `BRR = retried_and_succeeded / total_blocks`.

**Wall-clock latency per task (Latency_task)**  
Time from `claude` process start to process exit for a given task, in seconds.  
Written to `per_task_metrics.json` as `wall_clock_seconds` by the harness.

**Hook overhead p50/p95 (HookOverhead)**  
Per-call latency for the hook subprocess only: `hook_events.jsonl` field `hook_duration_ms` (written by the harness wrapper that timestamps before/after `pre_edit_guard.py` exits). Aggregate p50 and p95 across all treatment-arm tool calls.

**Token cost per task (Tokens)**  
`input_tokens` and `output_tokens` from Claude's usage metadata, summed per task.  
Written to `per_task_metrics.json` by parsing `claude_transcript.jsonl` for `usage` fields.

**Novelty Drift (NovΔ)**  
`ImpactReport.coherence_delta` on the final submitted patch vs. base commit. A large value indicates the final patch is semantically distant from the repo's existing code style.  
Computed by calling `score_change(file_path, base_src, final_patched_src)` for each touched file and averaging `coherence_delta`.  Written to `per_task_metrics.json` as `novelty_drift_mean`.

---

## 4. Protocol

### 4.1 Fixed parameters for both arms

All parameters below are identical across treatment and control to eliminate confounds.

| Parameter | Value |
|-----------|-------|
| Model | `claude-opus-4-7` (Anthropic API, not y-router; both arms hit the same endpoint) |
| Temperature | `0.0` (greedy; deterministic given same inputs) |
| System prompt | `/Users/jakubsikora/Repos/personal/reasoning-core/eval/prompts/system_prompt.txt` (identical file, pinned SHA) |
| Max turns | 40 |
| MCP servers (control) | None |
| MCP servers (treatment) | `hybrid-reasoner` as in `.claude/settings.json` |
| `S2_FAIL_CLOSED` | `0` (fail-open) to avoid spurious task failures from sidecar CPU latency |
| `S2_TIMEOUT` | `30` |
| Python version | 3.11 (pinned via `.python-version` in eval harness dir) |
| Seed (task order) | Drawn from `random.seed(42)` before shuffling task list |

### 4.2 Order randomization

Tasks are shuffled once (seed 42) and run in that order for **both** arms. Treatment and control for a given task run sequentially (control first, treatment second) to avoid any state leakage between tasks. There is no shared filesystem between runs; each task gets its own scratch clone of the target repo at `{EVAL_SCRATCH_DIR}/{task_id}/{arm}/`.

`EVAL_SCRATCH_DIR=/tmp/reasoning-core-eval`

### 4.3 Per-task harness

Entry point: `/Users/jakubsikora/Repos/personal/reasoning-core/eval/run_task.sh`

```
Usage: eval/run_task.sh <task_id> <arm>
  task_id: e.g. "django__django-11099"
  arm:     "control" | "treatment"

Writes to: /Users/jakubsikora/Repos/personal/reasoning-core/eval/results/<task_id>/<arm>/
  per_task_metrics.json   — all scalar metrics for this task+arm
  hook_events.jsonl       — one JSON line per PreToolUse fire (treatment only)
  claude_transcript.jsonl — one JSON line per Claude message/tool call
  test_results.json       — pytest outcome: gold_tests_passing, newly_failing_tests[]
  patch.diff              — the final diff applied to the repo
  sidecar.log             — stdout/stderr from the S2 sidecar process (treatment only)
```

Harness steps (executed by `run_task.sh`):

1. Clone target repo at the task's `base_commit` SHA into `$EVAL_SCRATCH_DIR/{task_id}/{arm}/repo/`.
2. If arm == treatment: start the sidecar (`bash scripts/start-sidecar.sh`) in background, wait for `/health` → `model_loaded:true`, record PID.
3. Launch `claude` CLI with `--model claude-opus-4-7 --no-hooks` (control) or without `--no-hooks` (treatment), redirecting stdin from the task's problem statement file, capturing stdout/stderr to `claude_transcript.jsonl`.
4. On exit: run the repo's full test suite (`python -m pytest` with `--tb=no -q --timeout=60`) in the patched working tree, write `test_results.json`.
5. Run `eval/scripts/ast_edit_distance.py`, `eval/scripts/cyclomatic_delta.py`, and `score_change` post-hoc to populate `per_task_metrics.json`.
6. If arm == treatment: kill the sidecar process.
7. Write `wall_clock_seconds` to `per_task_metrics.json`.
8. Remove `$EVAL_SCRATCH_DIR/{task_id}/{arm}/repo/` to reclaim disk.

### 4.4 Aggregation

After all 200 runs complete, run:

```
python3 /Users/jakubsikora/Repos/personal/reasoning-core/eval/aggregate.py \
  --results-dir /Users/jakubsikora/Repos/personal/reasoning-core/eval/results/ \
  --out /Users/jakubsikora/Repos/personal/reasoning-core/eval/report.json
```

`aggregate.py` computes:
- Per-metric paired differences `d_i = metric_i(treatment) − metric_i(control)` for each task i.
- Bootstrap 95% CI on `mean(d_i)` using 10 000 resamples with replacement.
- Two-sided Wilcoxon signed-rank statistic and p-value for each metric (using `scipy.stats.wilcoxon`).
- Holm-corrected p-values across all 10 metrics (see Section 5).

---

## 5. Statistical Analysis

### 5.1 Sample size and power

Covered in Section 2. n = 100 pairs, power ≈ 0.87 for the primary effect size δ = 0.15 at α = 0.05.

### 5.2 Test statistic

Primary analysis: two-sided Wilcoxon signed-rank test on paired differences `d_i = RR_i(control) − RR_i(treatment)` (binary per task: 1 if regression, 0 if not). For a binary outcome this reduces to a sign test; Wilcoxon is used for consistency with continuous secondaries.

For continuous secondary metrics: two-sided Wilcoxon signed-rank on paired differences.

### 5.3 Confidence intervals

Bootstrap 95% CI (BCa method, 10 000 resamples) on `mean(d_i)` for all metrics. Computed by `aggregate.py` using `scipy.stats.bootstrap`.

### 5.4 Multiple-comparison correction

10 metrics are tested (Section 3). Apply Holm-Bonferroni step-down correction across all 10 p-values. A metric is declared significant at familywise α = 0.05 only if its Holm-corrected p < 0.05.

```python
from statsmodels.stats.multitest import multipletests
reject, p_adj, _, _ = multipletests(raw_pvalues, alpha=0.05, method='holm')
```

### 5.5 Handling errors, timeouts, and ties

**Do not drop any task.** Errors and timeouts are informative.

| Event | Coding |
|-------|--------|
| Claude times out (> 30 min wall clock) | `resolved = False`, `regression_detected = True` (a non-submitted patch has regressed relative to baseline). `timeout = True` field added. |
| Sidecar fails to start (treatment arm) | Task is flagged `sidecar_failed = True`; treated as fail-open (hook did not fire); still included in RR computation. Reported separately in the operational summary. |
| pytest segfault / environment error | `test_error = True`; task excluded from RR computation but **included in task count denominator** with `RR = 0.5` (conservative imputation) and noted in report. |
| Hook fires but sidecar unreachable (fail-open) | `hook_fired = True`, `hook_blocked = False`, `sidecar_unreachable = True`; included in FPBR/TPBR denominator with block = False. |
| Exact ties in Wilcoxon | `scipy.stats.wilcoxon` default: zero-method='wilcox' (ties at zero excluded from ranking). |

---

## 6. Operational

### 6.1 Runtime and cost estimate

| Item | Value |
|------|-------|
| Tasks | 100 |
| Arms | 2 (control + treatment) |
| Total Claude runs | 200 |
| Estimated tokens per run (input + output) | ~80 000 (based on SWE-bench median task + 40-turn budget) |
| Total tokens | 200 × 80 000 = 16 000 000 |
| Opus 4.7 pricing (as of 2026-05) | $15 / M input + $75 / M output (approximate; 70/30 split assumption) |
| Estimated input tokens | 11 200 000 → $168 |
| Estimated output tokens | 4 800 000 → $360 |
| **Total estimated cost** | **~$528** |
| Wall-clock per run (with sidecar ~3s/call, ~15 tool calls/task) | ~8 min control, ~12 min treatment |
| Total wall-clock (sequential) | ~33 hours |
| Parallelism recommendation | 4 parallel workers (2 control, 2 treatment) → ~9 hours |

Sidecar Mamba forward pass on macOS arm64 CPU: ~3s per `/score` call. At 15 tool calls per task × 100 tasks = 1 500 calls × 3s = ~75 min additional latency in treatment arm. This is within the 35s hook timeout configured in `.claude/settings.json`.

### 6.2 Logs captured

| Artifact | Path | Contents |
|----------|------|----------|
| Claude transcript | `eval/results/{task_id}/{arm}/claude_transcript.jsonl` | One JSON line per Claude API response; includes `usage`, `content`, `tool_use`, `tool_result` blocks |
| Hook events | `eval/results/{task_id}/treatment/hook_events.jsonl` | One line per PreToolUse fire: `{task_id, file_path, tool_name, exit_code, hook_duration_ms, sidecar_response, timestamp_iso}` |
| Sidecar log | `eval/results/{task_id}/treatment/sidecar.log` | stdout+stderr from `uvicorn` process; includes per-request latency from FastAPI access log |
| Test results | `eval/results/{task_id}/{arm}/test_results.json` | `{gold_tests_passing, newly_failing_tests, total_tests_run, duration_seconds}` |
| Per-task metrics | `eval/results/{task_id}/{arm}/per_task_metrics.json` | All scalar metrics from Section 3 |
| Aggregated report | `eval/report.json` | Cross-arm summary statistics, p-values, CIs, decision table outcome |

### 6.3 Report location

Final aggregated report: `/Users/jakubsikora/Repos/personal/reasoning-core/eval/report.json`  
Human-readable summary: `/Users/jakubsikora/Repos/personal/reasoning-core/eval/report.md` (generated by `aggregate.py --format markdown`)

### 6.4 Threats to validity

| Threat | Description | Mitigation |
|--------|-------------|------------|
| **Contamination** | Opus 4.7 may have seen SWE-bench Verified tasks in training data, giving inflated resolved rates for both arms equally. | Contamination affects both arms identically in a paired design; the *difference* in regression rate is still valid. Document as a limitation. |
| **Ordering effects** | Running control before treatment for each task means treatment may benefit from the repo being "pre-warmed" in the OS page cache. | Both arms clone to fresh directories under `$EVAL_SCRATCH_DIR`; no shared repo state. The `repo/` dir is deleted after each run. |
| **Mamba CPU latency causing hook timeouts** | The sidecar Mamba forward pass takes ~3s on arm64 CPU. The hook has a 35s timeout. If the sidecar is under load (e.g., GC pause), the hook could time out and fail-open, spuriously contributing zero blocks to treatment arm. | Log all timeout events in `hook_events.jsonl` (`exit_reason: timeout`). Report the fraction of fail-open-due-to-timeout separately. If > 5% of treatment tool calls are timeout-open, re-run those tasks with `S2_TIMEOUT=60`. |
| **Unsupported-language fail-open** | Files touched alongside `.py` files (e.g., `.cfg`, `.rst`) cause the hook to fail-open per the architecture's 415 contract. | Restrict task inclusion to gold patches that touch only `.py` files. Post-hoc, report the fraction of tool calls on non-Python files in `hook_events.jsonl`. |
| **Non-determinism in sidecar scoring** | `torch.no_grad()` + `model.eval()` + seeded forward pass should be deterministic on CPU, but floating-point order can differ across PyTorch versions. | Pin `torch==2.3.1` in `eval/requirements-eval.txt`. Run a calibration check: score the same before/after pair twice before each task batch and assert `AIS` is identical to 6 decimal places. |
| **Test suite flakiness** | Some repo tests are flaky; a test may fail without the patch causing the failure. | Run the base-commit test suite once before each task and record `base_failing_tests`. Exclude those from `newly_failing_tests` computation. Written to `test_results.json` as `pre_existing_failures`. |
| **Claude prompt injection via SKILL.md** | The `hybrid-reasoner` MCP tool exposes `human_summary` from the ImpactReport back to Claude in the treatment arm. A carefully crafted repo file could inject instructions via the summary. | The summary is generated entirely by `_summarize()` in `s2_core.py` from numeric fields; it contains no repo content. Not a practical vector. |

---

## 7. Decision Criteria

Results are evaluated after `aggregate.py` runs and Holm correction is applied.

| Metric | Threshold for "ship hook by default" | Notes |
|--------|--------------------------------------|-------|
| Regression Rate (RR) | `R_c − R_t ≥ 0.15` AND Holm-corrected p < 0.05 | Primary criterion. Must be met. |
| Resolved Rate (ResR) | `ResR_t − ResR_c ≥ −0.05` (treatment no worse than 5 pp below control) | Hook must not significantly hurt task completion. |
| Hook False-Positive Block Rate (FPBR) | `FPBR ≤ 0.10` (≤ 10% of tool calls on correct tasks blocked) | Above this, the hook is too aggressive. |
| Wall-clock latency overhead | `median(Latency_treatment) / median(Latency_control) ≤ 1.5` | Hook must not more than 1.5× the per-task wall-clock time. |
| Block-Recovery Rate (BRR) | `BRR ≥ 0.60` | If Claude can't recover from blocks > 40% of the time, the UX is unacceptable. |

**Ship decision**: ALL 5 criteria above must be satisfied simultaneously to recommend enabling the hook by default.

### Kill criteria — abandon or disable the hook if any of the following:

| Kill Condition | Threshold |
|----------------|-----------|
| Hook makes regression rate worse | `R_t − R_c > 0.05` (Holm p < 0.05) |
| False-positive block rate exceeds | `FPBR > 0.25` |
| Treatment resolved rate is materially worse | `ResR_c − ResR_t > 0.10` (Holm p < 0.05) |
| Sidecar timeout-induced fail-opens exceed | 15% of all treatment tool calls |
| Mean novelty drift is higher in treatment | `NovΔ_t > NovΔ_c` with Holm p < 0.05 (would indicate hook-blocked good edits push Claude toward weirder alternatives) |

**Inconclusive result**: if no kill criteria are triggered but the primary H1 is not confirmed, the hook remains **opt-in** (current default) pending a larger study or threshold re-calibration. The coherence threshold (`coherence_delta > 1.5`) and AIS threshold (< 0.4) are the primary tuning levers; re-calibration can be done offline by replaying `hook_events.jsonl` against different threshold values without re-running the full eval.
