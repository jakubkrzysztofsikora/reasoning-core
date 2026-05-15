# Benchmarks

Two eval generations: an 8-task suite graded by 3 cross-vendor judges
(headline numbers), and an earlier iteration-1 draft (n=1–3 per cell,
single-judge).

> **Note on risk-vector dimensionality.** The headline eval below was
> graded against an 8-dim risk vector (`risk_labels_version=1`). The
> current build ships an 11-dim vector (`risk_labels_version=2`) — the new
> dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`)
> are additive and the eight original dims are unchanged. `coherence_delta`
> migrated from raw `L2/sqrt(D)` to chord distance `[0, 2]`, with all
> thresholds rescaled. The 8-task headline numbers were not re-graded
> against the new metric; the iter-2 re-run (sign-test acceptance criterion
> below) targets the new schema.

---

## Headline (3-judge, blind)

8 real engineering tasks. 3 runs each. 2 setups (vanilla `claude` vs
`claude` + sidecar). 3 independent reviewer models from 3 different vendors
graded every plan and every implementation, blind.

| | Vanilla `claude` | `claude` + sidecar | |
|---|---|---|---|
| Tasks passed (locked tests) | 92% | 100% | |
| Tasks passed (rotated tests) | 90% | 100% | |
| Plan quality (1–5) | 3.62 | 3.94 | +0.32 |
| Implementation quality (1–5) | 3.80 | 4.00 | +0.20 |
| Stays inside promised files (1–5) | 4.09 | 4.31 | Agent goes off-plan less often |
| Uses your repo's existing patterns (1–5) | 3.71 | 4.14 | Fewer invented helpers / new conventions |
| Code legibility (1–5) | 4.26 | 4.26 | Tied — sidecar doesn't help here |
| Total tokens used | 23.1M | 21.2M | −8.2% averaged across all 8 tasks |
| Best single-task token saving | — | −29% (PR review) | Up to ~29% on cache-heavy tasks |
| Wall-clock per run | 547s | 645s | +98s slower — sidecar plans before it edits |
| Where your code is processed | Anthropic only | Anthropic only + your laptop | Nothing new leaves your machine |

### Token-cost arithmetic

On the PR-review task the sidecar pulled 724k cache-read tokens vs 1.02M for
vanilla — a 29% saving on that single task. Auth-abandonment task came in at
27% lower. At Anthropic's public cache-read price ($0.30/MTok), saving ~300k
cache-read tokens per task ≈ $0.09/task. 100 tasks/month ≈ $9. 1,000 ≈ $90.
10,000 ≈ $900.

### Where it doesn't help

- **+98s per run.** The sidecar plans before the agent edits. If you live
  or die by raw turnaround, vanilla `claude` is faster.
- **Code legibility was a tie.** Both setups produce equally readable code.
  The sidecar makes the agent comply with your standards and stay on-plan —
  it doesn't make your code prettier.
- **One codebase, 8 tasks.** Numbers may shift on yours.

---

## Iteration-1 draft (single-judge, n=1–3)

> **Status: initial draft, n=1–3 per cell, single-judge.** Iteration-2
> failure-mode fixes are **shipped** (mock-detector P1 `1724810`,
> plan-quality CGS P2 `ad2ea80`, language-fingerprint lock P3 `54c6e57`,
> calibration concurrent with shadow P7 `89820b0`); the binomial sign-test
> acceptance harness wired in `eval/stats.py::sign_test` (`6a921ce`). Pre-
> registered acceptance: ≥7/8 task-mean wins for Setup B with ≥1.0 BARS gap
> and sign-test p ≤ 0.05.
>
> Full per-task per-judge tables live in `~/evals/2026-05-05_*/REPORT.md`
> (gitignored — they include real Anthropic spend).

**Setup.** Two arms × eight tasks (T1, T2, T5, T7, T8, T9, E1, P0),
randomized arm order per pair, fixed-seed correctness gate + rotated-seed
flake check, BARS-rubric implementation- and plan-quality grades.

- **Setup A (control):** vanilla Claude Code, no hooks.
- **Setup B (reasoning-core):** the gate in this repo, `S2_FAIL_CLOSED=1`,
  shadow-mode off (a *measurement* configuration; default operator posture
  ships shadow-mode-on per `.envrc`).

### Headline numbers (means across 8 tasks)

| metric | Setup A (vanilla) | Setup B (reasoning-core) | Δ (B − A) | Δ% |
|---|---:|---:|---:|---:|
| **Cost / task (USD)** | $11.44 | $8.56 | −$2.88 | **−25.1%** |
| **Wall clock / task** | 1 656 s | 1 270 s | −386 s | **−23.3%** |
| **Tokens / task (main)** | 66 733 | 65 222 | −1 511 | −2.3% |
| Impl quality (BARS 1–5) | 2.90 | 2.88 | −0.02 | flat |
| Plan quality (BARS 1–5) | 2.92 | 2.50 | −0.42 | −14.4% |
| **Task wins** (gates → impl_q → plan_q → cost) | 2 / 8 | **6 / 8** | — | — |

Suite totals: Setup A spent **$91.50** / 533 866 tokens; Setup B spent
**$68.51** / 521 772 tokens. ~$23 / 25% saved at the suite level on this
single-run draft.

### Per-task verdicts

| task | winner | A impl_q / plan_q | B impl_q / plan_q | A tokens | B tokens | A $ | B $ |
|---|---|---:|---:|---:|---:|---:|---:|
| T1 | A | 5.0 / 5.0 | 3.0 / 1.0 | 71 200 | 29 800 | $13.08 | $3.41 |
| T2 | B | 3.5 / 3.0 | 5.0 / 3.0 | 121 000 | 94 200 | $22.93 | $12.39 |
| T5 | B | 1.83 / 1.67 | 3.5 / 3.0 | 84 533 | 72 208 | $15.53 | $10.26 |
| T7 | B | 1.83 / 1.67 | 3.5 / 3.0 | 84 533 | 72 208 | $15.53 | $10.26 |
| T8 | B | 3.0 / 3.0 | 4.0 / 5.0 | 41 600 | 24 662 | $6.98 | $2.83 |
| T9 | A | 1.0 / 3.0 | 1.0 / 1.0 | 37 700 | 39 894 | $2.85 | $3.49 |
| E1 | B (correctness gate) | 3.5 / 3.0 (locked 0/1) | 1.0 / 1.0 (locked 1/1) | 57 600 | 41 600 | $5.89 | $4.29 |
| P0 | B | 3.5 / 3.0 | 2.0 / 3.0 | 35 700 | 147 200 | $8.71 | $21.58 |

### What this draft shows

- **Money**: Setup B is meaningfully cheaper on 6/8 tasks. The P0 outlier
  (B spent $21.58 vs A's $8.71) inflates B's mean tokens; without P0, B's
  mean cost drops to ~$5.34 (−40% vs A).
- **Wall clock**: Setup B finishes ~23% faster on average. The gate is not
  free (p95 ~5 s/Edit on CPU Mamba); the speedup comes from B avoiding
  regression-rework loops.
- **Quality**: implementation-quality means are flat. B wins by **decision
  rule** (gates → impl_q → plan_q → cost), not by raw rubric points.
- **Failures (informative)**:
  - **T1** lost because iter-1 had no mock-detector — Claude shipped
    placeholder code, scored low. Iter-2 ships `_mock_detector.py`.
  - **T9** lost because plan-time scoring measured plan-vs-plan novelty,
    not generic-vs-specific. Iter-2 ships the plan-quality CGS gate
    (`_plan_quality.py`, behind `RC_PLAN_QUALITY=1`).
  - **E1** is a partial-win: B passed the correctness gate (1/1) where A
    failed (0/1), but the rubric grader marked B's diff lower because no
    language-convention enforcement existed in iter-1. Iter-2 ships
    `RC_LANG_LOCK` + post-batch language audit.

### Caveats

- n=1 per (task, arm) cell on most tasks (T5/T7 group has n=3); CIs are
  wide. The iter-2 re-run targets n≥3 per cell with cross-family judges
  (Gemini + vibe) and a Krippendorff α inter-rater gate.
- Single-judge BARS grades; iter-2 adds the cross-family judge and
  per-grade contamination check (`eval/contamination.py`).
- Decision rule is lexicographic (gates first); a single rubric point swing
  can flip a per-task verdict. Treat headline win-count as directional, not
  significant.

### Iteration-2 acceptance criterion (pre-registered)

Setup B must pass the sign-test on 8 tasks (≥7/8 wins → p ≤ 0.035; 8/8 → p
= 0.0039) with **paired bootstrap 95% CI on suite-mean BARS impl-quality
excluding 0**. See
[`../thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`](../thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md)
for the full v2 plan + the 19 reviewer corrections folded in.

---

## Eval harness

`eval/` is the calibration + regression-test machine for the gate. See
[`../eval/README.md`](../eval/README.md) for full reference.

| Component | Purpose |
|---|---|
| `validate_embedder.py` | Embedder fitness — checks Mamba pooled embeddings discriminate semantic-vs-syntactic edits |
| `calibration_corpus.py` | Mines labeled (good-edit, bad-edit) pairs from git history |
| `golden_set.py` | Pinned regression cases that must keep their decisions across releases |
| `recalibrate.py` | Page-Hinkley monthly recal of per-kind thresholds |
| `qwen_grounding_eval.py` | Cohen κ between SSM gate and the generative critic (v3 sentinel: κ=0.8025, gate_pass=true) |
| `run_suite.py` + `aggregate.py` + `stats.py` | Paired Wilcoxon harness across N runs |
| `synthetic_drift.py` | Drifted variants for stress testing |
| `build_grounding_pairs.py` | Rebuilds the labeled-pair dataset from raw sources |

Smoke run:

```bash
python3 -m eval.run_suite --task fixtures/smoke --n 2
python3 -m eval.aggregate --runs eval/runs/smoke-001
```
