# Benchmarks

Two eval generations: an 8-task suite graded by 3 cross-vendor judges
(headline numbers), and an earlier iteration-1 draft (n=1–3 per cell,
single-judge).

> **Evidence status:** The headline figures below are historical controlled-study
> results from one codebase. They were graded against `risk_labels_version=1`,
> while the current build uses version 2, and were not re-graded after the
> metric migration. They must not be presented as current real-usage quality,
> cost, or regression evidence. The committed current smoke run is explicitly
> inconclusive; the next evidence source is the labeled real-session dataset.

> **Note on risk-vector dimensionality.** The headline eval below was
> graded against an 8-dim risk vector (`risk_labels_version=1`). The
> current build still emits an 8-dim vector by default
> (`risk_labels_version=2`); three additional dims
> (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are
> emitted only when `RC_PROJECT_INDEX=1` and a session baseline is
> registered. `coherence_delta` migrated from raw `L2/sqrt(D)` to chord
> distance `[0, 2]`, with all thresholds rescaled. The 8-task headline
> numbers were not re-graded against the new metric; the iter-2 re-run
> (sign-test acceptance criterion below) targets the new schema.

---

## Historical headline (3-judge, blind; not a current product guarantee)

The historical study used 8 real engineering tasks, 3 runs each, and 2 setups (vanilla `claude` vs
`claude` + sidecar). 3 independent reviewer models from 3 different vendors
graded every plan and every implementation, blind.

These values are retained for reproducibility and hypothesis generation. They
do not establish that the current default installation improves real repository
outcomes.

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
| Wall-clock per run | 547s (superseded) | 645s (superseded) | +98s — see Headline numbers table below for the canonical iter-1 measurement |
| Where your code is processed | Anthropic only | Anthropic only + your laptop | Nothing new leaves your machine |

### Historical token-cost arithmetic

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

### Headline numbers — RETRACTED 2026-09-19 (8-cell set)

> **This table is retracted.** All 8-cell aggregates below were computed
> over the retracted T5, T7, and P0 rows (see "Per-task verdicts
> (Iteration 1 draft — RETRACTED 2026-09-19)" below). They are retained
> in this box only to preserve the original draft numbers for
> reproducibility; they must not be cited as evidence of any product
> claim. See the next subsection for the surviving-5 aggregates and
> the withdrawn wall-clock note.

| metric | Setup A (vanilla) | Setup B (reasoning-core) | Δ (B − A) | Δ% |
|---|---:|---:|---:|---:|
| **Cost / task (USD)** | $11.44 | $8.56 | −$2.88 | **−25.1%** |
| **Wall clock / task** | 1 656 s | 1 270 s | −386 s | **−23.3%** |
| **Tokens / task (main)** | 66 733 | 65 222 | −1 511 | −2.3% |
| Impl quality (BARS 1–5) | 2.90 | 2.88 | −0.02 | flat |
| Plan quality (BARS 1–5) | 2.92 | 2.50 | −0.42 | −14.4% |
| **Task wins** | 2 / 8 (retracted) | **6 / 8** (retracted) | — | — |

Suite totals: Setup A spent **$91.50** / 533 866 tokens; Setup B spent
**$68.51** / 521 772 tokens. All numbers in this block derive from
retracted rows and are withdrawn.

### Headline numbers (recomputed across surviving 5 cells)

> **These are the post-retraction aggregates.** Only the 5 cells for which
> per-task evidence still exists (T1, T2, T8, T9, E1) are averaged. Wall
> clock is **withdrawn** because per-task wall-clock was never recorded
> separately from the retracted runs and cannot be reconstructed from
> retraction evidence. Token and cost averages are derived directly from
> the per-task table below; both arms are n=1 per cell on T8/T9, n=3 on
> T2/E1, n=1 on T1.

| metric | Setup A (vanilla) | Setup B (reasoning-core) | Δ (B − A) | Δ% |
|---|---:|---:|---:|---:|
| **Cost / task (USD)** | $10.35 | $5.28 | −$5.07 | **−49.0%** |
| **Wall clock / task** | withdrawn | withdrawn | — | — |
| **Tokens / task (main)** | 65 820 | 46 031 | −19 789 | **−30.1%** |
| Impl quality (BARS 1–5) | 3.20 | 2.80 | −0.40 | −12.5% |
| Plan quality (BARS 1–5) | 3.40 | 2.20 | −1.20 | −35.3% |
| **Task wins** (lexicographic rule) | 2 / 5 | 3 / 5 | — | — |
| **Task wins** (with ≥ 1.0 BARS gap requirement) | 2 / 5 | 2 / 5 | — | — |
| **Sign-test p-value (exact binomial, one-sided)** | — | **p = 0.50** | — | — |
| **Sign-test p-value (exact binomial, two-sided)** | — | **p = 1.00** | — | — |

Suite totals (surviving 5 cells): Setup A spent **$51.73** / 329 100
tokens; Setup B spent **$26.41** / 230 156 tokens. $25.32 / 49% lower at
the suite level on the surviving-5 evidence.

The exact two-sided p-value of 1.00 (coin flip) means the surviving
5-cell evidence does not establish a directional advantage for either
arm at any conventional significance threshold. The pre-registered
iter-2 acceptance criterion (≥7/8 wins, paired bootstrap 95% CI
excluding 0 on impl quality) requires an additional ≥3 cells of
n≥3 evidence before any directional claim can be made.

### Per-task verdicts (Iteration 1 draft — RETRACTED 2026-09-19)

> **Status: data-integrity retraction.** A hostile review surfaced three
> issues with this iteration-1 table:
>
> 1. **T5 and T7 rows are byte-identical** (84 533 tokens, $15.53, same
>    impl/plan quality) — physically impossible for two distinct
>    end-to-end LLM agent runs. Both rows were copy-pasted from a
>    single recorded run during initial table assembly.
> 2. **P0 winner was inverted** against the pre-registered decision
>    rule. Under (gates → impl_q → plan_q → cost), Setup A wins P0
>    decisively (3.5 vs 2.0 impl, 4× fewer tokens, 2.5× cheaper) — not
>    Setup B. Correcting P0 drops Setup B to 5/8 task-mean wins,
>    rendering the n=8 sign test indistinguishable from a coin toss
>    (two-sided p ≈ 0.73).
> 3. The two latency tables on this page contradict each other by
>    484 seconds per run (Table 1 says +98s slower; Table 2 says
>    −386s faster).
>
> Per-task JSONL evidence for what was actually recorded lives in
> `eval/runs/` (gitignored) and is referenced from `eval/runs/<run-id>/`
> manifests. The doc itself already disclaimed this section as
> "draft n=1–3" and "headline win-count is directional, not
> significant", but the duplicated rows made even the directional
> claim unreliable. The retracting commit is
> `audit-hostile/2026-09-19-fixes`.
>
> The pre-registered iter-2 acceptance harness
> (`docs/EVAL_DESIGN.md` §7, `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`)
> targets n≥3 per cell with cross-family judges. Until that runs and
> publishes, treat this section as illustrative, not evidentiary.

| task | winner | A impl_q / plan_q | B impl_q / plan_q | A tokens | B tokens | A $ | B $ |
|---|---|---:|---:|---:|---:|---:|---:|
| T1 | A | 5.0 / 5.0 | 3.0 / 1.0 | 71 200 | 29 800 | $13.08 | $3.41 |
| T2 | B | 3.5 / 3.0 | 5.0 / 3.0 | 121 000 | 94 200 | $22.93 | $12.39 |
| T8 | B | 3.0 / 3.0 | 4.0 / 5.0 | 41 600 | 24 662 | $6.98 | $2.83 |
| T9 | A | 1.0 / 3.0 | 1.0 / 3.0 | 37 700 | 39 894 | $2.85 | $3.49 |
| E1 | B (correctness gate) | 3.5 / 3.0 (locked 0/1) | 1.0 / 1.0 (locked 1/1) | 57 600 | 41 600 | $5.89 | $4.29 |

> T5, T7, and P0 are omitted from the table above — see the
> retraction note for the reasons. The remaining 5 cells are still
> n=1 and should be treated as illustrative.

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


## See also

- [`docs/AUDIT_RESPONSE_2026_09_19.md`](AUDIT_RESPONSE_2026_09_19.md) —
  response to the 2026-09-19 hostile review that surfaced the
  T5/T7/P0 data-integrity issues. The retraction block above lives at
  commit `841211b` on branch `audit-hostile/2026-09-19-fixes`.
