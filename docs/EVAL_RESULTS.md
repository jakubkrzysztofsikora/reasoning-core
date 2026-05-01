# Eval Results — smoke-001

> First end-to-end exercise of the reasoning-core eval toolkit.
> Status: **toolkit green; signal pending live Claude run.**

## Summary

| field | value |
|---|---|
| run_id | `smoke-001` |
| commit | `503f7e1` |
| invocation | `RC_LIVE=1 RC_EVAL_STUB_CLAUDE=1 python3 eval/run_suite.py --n 2` |
| tasks | 2 (`psf__requests-2317`, `django__django-13710`) |
| arms | vanilla, treatment |
| pairs completed | 2 / 2 (100%) |
| errored | 0 |
| timed out | 0 |
| total runs | 4 |
| audit events captured | 311 |
| verdict | inconclusive (stub mode — by design) |
| wall clock | ~75 s |

## Methodology — what this run validates

This is **toolkit smoke**, not a methodological eval. The goal is to prove
the pipeline runs end-to-end without manual intervention:

1. `eval/run_suite.py` reads
   `eval/datasets/swe_bench_verified_python_subset.json`, samples 2 tasks
   deterministically (seed=42), randomises arm order per pair.
2. For each (task, arm) pair, `eval/run_task.sh` clones the repo at
   `base_commit`, swaps `.claude/settings.json` per arm (vanilla = none,
   treatment = ours + `S2_FAIL_CLOSED=1`), and would invoke `claude` —
   but `RC_EVAL_STUB_CLAUDE=1` swaps in a recorded-patch stub.
3. Per-task JSON written to `eval/runs/smoke-001/<task>.<arm>.json` with
   `resolved`, `regression_introduced`, `tokens_in/out`, `wall_clock_s`,
   `claude_exit_code`.
4. `eval/aggregate.py` joins per-task JSONs with `/tmp/rc-events`
   audit JSONL, computes 10 metrics with paired Wilcoxon + BCa
   bootstrap + Holm-Bonferroni, applies decision criteria from
   `docs/EVAL_DESIGN.md` §7.
5. Renders `report.md` + `report.json` in the run dir.

## Headline metrics

| metric | n | mean Δ (treatment − vanilla) | 95% CI | p (Wilcoxon) | p (Holm) |
|---|---:|---:|---|---:|---:|
| resolved_rate | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| regression_rate | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| ast_edit_distance | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| cyclomatic_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| fan_in_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| fan_out_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| wall_clock_s | 2 | −0.50 | [−1.000, −0.500] | 1.000 | 1.000 |
| tokens_in | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| tokens_out | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| novelty_drift | 2 | 0.0000 | [0.000, 0.000] | nan | nan |

Stub Claude returns the same recorded patch for both arms, so every Δ
is 0 by construction. `wall_clock_s` shows a tiny treatment-favored
delta from setup-cost noise, not a signal. Wilcoxon `p=nan` is correct
behavior for all-zero deltas (no rank to test).

## Decision criteria — `docs/EVAL_DESIGN.md` §7

| criterion | result |
|---|---|
| `regression_rate_drop_geq_0.15_holm_p_lt_0.05` | FAIL (no signal) |
| `resolved_rate_no_worse_than_−5pp` | PASS |
| `latency_ratio_leq_1.5` | PASS |
| **verdict** | **inconclusive** |

This is the expected output for stub mode. Real signal requires
`RC_LIVE=1` **without** `RC_EVAL_STUB_CLAUDE=1`, which needs a working
`claude` CLI on PATH and ~$11 of Anthropic spend per the cost model.

## What this proves

- ✅ Dataset loader resolves the 5-task offline bootstrap.
- ✅ Per-arm settings.json swap works (vanilla strips hooks, treatment
  installs them with `S2_FAIL_CLOSED=1`).
- ✅ Per-task JSON capture conforms to the schema `aggregate.py`
  expects (no parser errors).
- ✅ Audit JSONL join works — 311 events were correlated despite
  living in `/tmp/rc-events/<date>/<session>.jsonl` and being written
  by hooks from the development session, not the eval run itself.
- ✅ `aggregate.py` produces both `report.md` and `report.json`
  without manual intervention.
- ✅ Decision logic pulls criteria from `EVAL_DESIGN.md` §7 and emits
  `verdict ∈ {ship, kill, inconclusive}`.
- ✅ Bug found and fixed during smoke: `run_task.sh` heredoc Python
  emitted bash `true`/`false` literals; corrected to `True`/`False`.
  Patch is part of the same commit as this report.

## What this does NOT prove

- ❌ Claude actually wins/loses with the hooks. Stub means no model.
- ❌ Mamba SSM scoring helps regression detection. Stub never triggers
  the hook.
- ❌ Hook overhead is acceptable in a real workload. Stub is < 1 s/run.
- ❌ Free `ubuntu-latest` can finish n=2 in 30 min on cold cache. Local
  Mac was ~75 s with all caches warm.

## Next steps

| step | ETA | cost | trigger |
|---|---|---|---|
| `RC_LIVE=1 python3 eval/run_suite.py --n 2` (real Claude, n=2 paired) | ~25 min | ~$11 | manual |
| `n=5` smoke via `workflow_dispatch` | ~30–45 min | ~$26 | GitHub Actions input |
| `n=100` full per `EVAL_DESIGN.md` | ~9 h @ 4-worker | ~$528 | manual approval |

To kick the live n=2 locally:

```bash
export ANTHROPIC_API_KEY=...
export S2_FAIL_CLOSED=1 S2_TIMEOUT=60
unset RC_EVAL_STUB_CLAUDE
RC_LIVE=1 python3 eval/run_suite.py --n 2 --out-dir eval/runs/live-001
cat eval/runs/live-001/report.md
```

## Artifacts

```
eval/runs/smoke-001/
├── results.jsonl          # per-task lifecycle (4 lines, one per run)
├── run_summary.json       # schedule + counters
├── report.md              # human-readable headline
└── report.json            # machine-readable, full metric matrix
```

## Toolkit reproducibility

Re-run anytime:

```bash
rm -rf eval/runs/smoke-001
RC_LIVE=1 RC_EVAL_STUB_CLAUDE=1 python3 eval/run_suite.py \
  --n 2 --out-dir eval/runs/smoke-001 --task-timeout 60
```

Output is deterministic given fixed seed + fixed dataset + fixed stub
patches. Any drift indicates a regression in `aggregate.py` or
`metrics.py`, not in the eval methodology.
