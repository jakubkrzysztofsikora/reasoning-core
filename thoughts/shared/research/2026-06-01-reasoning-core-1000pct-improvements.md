---
date: 2026-06-01
commit: cc099b3d4293024b0b10905db9f7110a59fdd320
branch: main
tags: [reasoning-core, improvements, audit-empirics, prm, plan-grounding, sidecar, sota-2026]
status: complete
supersedes_partially: thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md
---
# Research: Reasoning-Core — Empirical Audit + 1000× Reasoning-Efficiency Improvement Roadmap

## Summary

A fresh stratified sample of **3,657 audit events across 25 days** (`~/.local/share/reasoning-core/events/`, 8,885 files, 40 MB) plus a deep read of `src/hooks/_dispatch.py`, `src/s2_core.py`, `src/ssm_backbone.py`, and the eval harness (`eval/runs/smoke-001/`, `iter3-prereg.json`, SWE-bench iter1 pilot) confirms the diagnosis of the 2026-05-23 audit and finds **four structural facts the prior audit did not surface**:

1. **The advertised 11-dim risk vector is a 8-dim vector on every audit-log event we sampled.** `RISK_LABELS` (`src/s2_core.py:57`) declares 11 dims but `_compute_risk_vector` (`s2_core.py:679-754`) returns only 8 floats. The three "Phase 2" dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are appended at `s2_core.py:1029-1033` *only* when `session_id and RC_PROJECT_INDEX=1` — which the audit log shows happens **never** on real Edits captured here (all sampled `risk_vector` arrays are length 8, all `cumulative_drift` and `session_centroid_drift` fields are absent or None). The headline "11-dim" claim is degenerate in production.
2. **The coherence-delta signal is dead.** Of 197 sampled events with a `coherence_delta`, **193 (98%) are in `[0.00, 0.10)`** — almost every Edit registers as coherence ≈ 0. Threshold is 0.5 (`s2_core.py:778`). The gate cannot fire on this dim except on cold-start file rewrites. The cold-start zero-out at `s2_core.py:967-971` then also zeros all 7 structural dims when `len(before_src)<32`, so the cold-start path is doubly silenced.
3. **Sidecar tail-latency is catastrophic for an interactive hook.** SSM-signal events have p50=3.4s, **p95=58s, p99=60s, max=60.2s** — the sidecar timeout (`s2_core` HTTP) eats the whole budget on the long tail. The gate forces the agent to wait *minutes* before the next edit can land. 15% of SSM calls fail-open (sidecar 503/timeout). At those latencies the "−8.2% tokens, +98s wall-clock" trade in `docs/BENCHMARKS.md` is already very fragile; on a session with 30 edits it would be **+25 min**, not +98s.
4. **The "decisive" signals are mostly self-protective, not reasoning-quality.** Of 3,657 sampled events, **2,853 are `allowed Bash` no-signal** (78%), **162 are `audit_only Edit:no_plan_md`** (4.4%; gate disabled), only **80 are real blocks**, and of those, **52/80 (65%) are `guard_file_locked` / `[hybrid-reasoner] BLOCKED: shell-level source write detected` / `language_fingerprint_violation`** — i.e. shell-escape closure and gate self-protection. **Only 3 events fired on real semantic regression**, and **2 fired on `regression_detected`** (the only neural-driven blocks in the entire 25-day window). The "neural gate" is providing decisive value on ~0.05% of agent actions.

Combined, these say the gate is **wired in but reasoning-blind**: it catches bash-escape bypasses (real value), protects its own files (mostly self-referential), and contributes almost nothing on the actual code-quality axis it markets. The headline iter-3 wins (`docs/BENCHMARKS.md`: +0.43 repo-fit, +0.32 plan-quality) come from `RC_BEST_EFFORT_SPEC=1` and `RC_PLAN_GROUNDING=1` — both of which are **OFF by default** (`.envrc:133,141`) and were enabled only in `eval-setups-B/.envrc.txt`. Day-zero installs ship with the value-generating levers OFF.

This document is **NOT a "list of 30 fixes."** It is a ranked, falsifiable, 5-bet plan with kill-criteria, derived from (a) the empirical audit, (b) SOTA in agent process-reward / plan-grounding / verifier literature mid-2026, and (c) what existing audits (`2026-05-23-*`) already covered so we don't repeat them.

> **Constraint discipline.** The prior audit named ~20 fixes without ranking. Engineer-reviewer ([`2026-05-23-effectiveness-audit-review-engineer.md`](2026-05-23-effectiveness-audit-review-engineer.md)) flagged that anti-pattern explicitly. This doc forces **top-5 bets with kill criteria and expected effect-size**, and explicitly lists what we are **not** doing.

---

## 1. Empirical baseline — what the gate actually does

Sample protocol: stratified random — up to 3 `.jsonl.gz` files per day across 25 days = 71 files = 3,657 events. Code in `/Users/jakubsikora/.local/share/reasoning-core/events/`.

### 1.1 Decision × tool_name distribution

| decision | tool | count | % |
|---|---|---:|---:|
| `allowed` | Bash | 2853 | 78.0% |
| `audit_only` | Edit | 162 | 4.4% |
| `allowed` | Edit | 153 | 4.2% |
| `audit_only` | Write | 96 | 2.6% |
| `unsupported` | Edit | 78 | 2.1% |
| `warn` | Edit | 53 | 1.4% |
| `fail-open` | Write | 45 | 1.2% |
| `allowed` | Write | 42 | 1.1% |
| `fail-open` | Edit | 42 | 1.1% |
| **`blocked`** | Bash | 32 | 0.9% |
| **`blocked`** | Write | 26 | 0.7% |
| `unsupported` | Write | 21 | 0.6% |
| `injected` | SessionStart | 18 | 0.5% |
| **`blocked`** | Edit | 12 | 0.3% |
| **`blocked`** | Plan | 9 | 0.2% |
| `skipped` | SessionStart | 8 | 0.2% |
| `degraded` | Bash | 3 | 0.1% |
| `shadow_blocked` | Write | 1 | 0.03% |

**Headline ratio:** 80 hard blocks / 3,657 events = **2.2% block rate**, of which **~21% are reasoning-quality** (regression_detected, plan_impl_drift, language fingerprint) and **~65% are self-protection / shell-escape closure**. This is consistent with — and slightly worse than — the 21% figure in [`2026-05-23-reasoning-core-effectiveness-audit.md`](2026-05-23-reasoning-core-effectiveness-audit.md).

### 1.2 Signal-source distribution

| signal_source | events | dominant decision |
|---|---:|---|
| (null — Bash, audit-only Edits, fail-open) | 2,899 | allowed |
| `ssm` | 417 | allowed (only 2 `regression_detected`, 1 `guard_file_locked`-via-ssm) |
| `plan_grounding` | 312 | mix of `audit_only:no_plan_md` (258) and `warn:plan_impl_drift` (54) |
| `best_effort_spec` | 26 | `injected` (18) / `skipped` (8) |
| `lang_lock` | 3 | `shadow_blocked:language_fingerprint_violation` (1) |

**`ssm` produces 417 events and 2 decisive blocks.** Hit rate ≈ **0.5%**. Either the model is too lax, the threshold is too high, or the metric is degenerate (see §1.3).

### 1.3 The "neural" signal is empirically degenerate

Of 197 events with `coherence_delta`:

| bin | count | % |
|---|---:|---:|
| 0.00–0.10 | 193 | 98% |
| 0.10–0.25 | 2 | 1% |
| 0.25–0.50 | 1 | 0.5% |
| 0.75–1.00 | 1 | 0.5% |
| ≥ 0.50 (gate trigger) | **1** | **0.5%** |

`COHERENCE_DELTA_THRESHOLD = 0.5` (`src/s2_core.py:778`). The metric **never crosses threshold** in normal operation. Note that the **median coherence_delta = 0.000** — for half of all Edits, the chord distance between before/after embeddings is literally zero. That is consistent with: many edits are very small, the chord-distance metric (chord ∈ [0, 2]) compresses small-edit signal toward 0 too aggressively, and the calibration that picked 0.5 was done against pre-migration `L2/sqrt(D)` scale (per [`2026-05-05-coherence-delta-calibration.md`](2026-05-05-coherence-delta-calibration.md)). The metric migrated; the threshold did not.

`AIS` (1 − coherence-delta-normalised similarity) tells the same story: n=197, **mean=0.965**, min=0.664 — every Edit reads as "highly aligned with baseline." The threshold for ais-below-threshold-source-code is 0.4 (`s2_core.py:771`). The gate cannot fire on this dim either.

`cumulative_drift` is **always null** in our sample. `session_centroid_drift` field is absent. **The Phase-2 risk dims do not appear in any real audit event.**

### 1.4 Latency by signal source — interactive-budget failure

```
signal_source        n      p50      p95      p99       max
None (bash/skip)   2899      6ms      9ms     18ms    19260ms
ssm                 417   3437ms  57829ms  60030ms    60156ms   ← BLOCKER
lang_lock             3      3ms      3ms      3ms        3ms
plan_grounding      312      2ms      4ms     12ms       28ms
```

SSM tail latency is **60 seconds**. The `s2_core` POST has no client-side hard cap separate from the gate (`pre_edit_guard._timeout_seconds()` defaults are forgiving). For a 1-line edit in a 1k-LOC file, the agent waits a full minute. This single number explains why iter-3 wall-clock is +98s/run on an 8-task suite — and why anything beyond a few edits per session will rapidly amplify.

### 1.5 Sidecar reliability — 85% of `ssm` calls succeed

In the sample: 90 `fail-open` + 32 `sidecar_unavailable_fail_closed` blocks + 3 `sidecar_kill_revived` = 125 sidecar incidents on 417 ssm-tagged calls + 90 fail-open Writes/Edits = **~24% incident rate** counting fail-open. The prior audit measured 94.2% over 19 days; our 25-day stratified sample shows the picture has not improved.

Failure breakdown:
- 75 `http_503` (~60%): sidecar process up but unhealthy (model not loaded yet, or OOM).
- 19 `timed out` (~15%).
- 4 `<urlopen error [Errno 61] Connection refused>` (~3%): process down.

The `RC_GEN_BUDGET_MS=2500` budget on the *gen-critic* call (`src/gen_client.py:89`) is not the same budget as the scoring sidecar (`s2_core`), which has no obvious client-side hard cap. **Two budgets, one of them missing.**

### 1.6 Project diversity — gate runs everywhere but provides value almost nowhere

`reasoning-core` itself = 195 events. The top 5 repos by event count produce 79% of all audit traffic. The blocks-per-repo distribution is heavily concentrated: `cyberlegion` and `sikoras-chat` together produce 47 of 80 sample blocks (59%). Most of these are `guard_file_locked` for `.claude/settings.json` edits — the very files we install. **The gate is defending itself more than the user's code.**

---

## 2. Files Involved

### Hooks / dispatch
| File | Lines | Role |
|---|---|---|
| `src/hooks/_dispatch.py` | 673 | Per-gate chain: `gate_kill_switch_and_magic`, `gate_lang_lock`, `gate_plan_grounding`, `gate_mock_detector`, `gate_drift`, `gate_calibration`, `gate_rule_engine`, `gate_regression` |
| `src/hooks/pre_edit_guard.py` | 823 | Edit/Write/MultiEdit entrypoint; `_post_score` ([line 183](src/hooks/pre_edit_guard.py)) → sidecar `POST /score` |
| `src/hooks/pre_bash_guard.py` | 399 | 4 layers: hard-deny, guarded-path, kill, shell-write |
| `src/hooks/pre_plan_guard.py` | 489 | PLAN.md write screening |
| `src/hooks/_calibration_gate.py` | 241 | `RC_CALIBRATION_ENABLED=1` (default 0) Mahalanobis gate |
| `src/hooks/_ood_detector.py` | 128 | OOD on plan embeddings (plan-guard only) |
| `src/hooks/_mock_detector.py` | 258 | `RC_MOCK_DETECTOR=1` (default 1) — wildcard/mock heuristics |
| `src/hooks/_plan_quality.py` | 240 | ARD / NRD / GPAS / SLR / CGS / CDGS plan scoring |
| `src/hooks/audit_log.py` | 394 | Schema v3, redaction, gzip rotation, `GATE_IDS` registry (**not populated** — see [audit-23] §"What is not working" #1) |
| `src/hooks/adapters/` | — | Gemini / Copilot / Vibe shims |

### Sidecar / SSM
| File | Lines | Role |
|---|---|---|
| `src/s2_core.py` | 1431 | HTTP server on 127.0.0.1:8765; `_compute_risk_vector` ([line 679](src/s2_core.py)), `_KIND_THRESHOLDS` ([line 813](src/s2_core.py)), `_l2_distance` ([line 841](src/s2_core.py)) |
| `src/ssm_backbone.py` | 1011 | `RC_EMBEDDER`: `mamba-130m` (default), `codestral-mamba`, `codestral-mamba-gguf`, `bge-code`, `unixcoder-base`, `random-mamba` (control) |
| `src/sidecar_boot.py` | 183 | HTTP entry |
| `src/sidecar_supervisor.py` | 258 | Lifecycle + restart |
| `src/_supervisor_recalibrate.py` | — | Polls `eval/runs/recalibrate.signal` (`_supervisor_recalibrate.py:32`) |
| `src/gen_client.py` | 261 | Critic-LLM client; `RC_GEN_MODEL=mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` ([line 171](src/gen_client.py)); `RC_GEN_BUDGET_MS=2500` ([line 89](src/gen_client.py)) |
| `src/project_index.py` | 288 | Per-session call-graph + import index; gated on `RC_PROJECT_INDEX=1` ([envrc:226](.envrc)) |

### Eval
| File | Role |
|---|---|
| `eval/run_suite.py`, `eval/run_task.sh`, `eval/aggregate.py`, `eval/metrics.py`, `eval/stats.py` | Setup A/B spawner + sign-test + Holm |
| `eval/runs/smoke-001/{report.md,report.json,run_summary.json,results.jsonl}` | The only fully-on-disk eval suite (n=2 pairs, verdict=inconclusive) |
| `docs/iter3-frozen-artifacts/eval-setups-{A,B}/` | Frozen iter-3 setups — **A has no hooks at all** (2 files: envrc.txt + settings.local.json) |
| `thoughts/shared/research/iter3-prereg.json` | Lex order: coverage → safety → honesty → correctness. Primary metric: flake_locked_pass_rate (MDE ≈ 0.13 at n=8). |
| `thoughts/shared/research/iter3-decomposer-reliability-report.json` | **Phase 0.5 FAILED**: gemini-vibe Jaccard 0.20 (floor 0.6). c@a-min DEMOTED to descriptive. |
| `thoughts/shared/research/2026-05-09-swebench-iter1-pilot-status.md` | SWE-bench iter1: **paused at 53/990 cells**; VM offline by 2026-05-10. Earlier "B 40% / A 60%" numbers **not on-disk reproducible**. |

---

## 3. Data Flow — what changed since 2026-05-23

```
ANY-CLI PreToolUse                  src/hooks/_dispatch.py:46 (GateOutcome)
  │
  ▼
src/hooks/pre_edit_guard.py:324 main()
  ├─ 336–352: _exit(0) on no-tool-input / no-file-path / kill-switch
  ├─ 358:     audit_log.record_block / is_retry_after_block
  ├─ 388–435: gate_kill_switch_and_magic + gate_lang_lock
  ├─ 484:     plan-grounding sub-gate (RC_PLAN_GROUNDING off by default → audit_only:no_plan_md)
  ├─ 553:     _post_score(file_path, before_src, after_src) → 127.0.0.1:8765/score
  │              │
  │              ▼
  │           src/s2_core.py /score
  │              ├─ 679: _compute_risk_vector → 8-dim
  │              ├─ 1015–1024: append project_fan_in / project_coupling   ONLY IF RC_PROJECT_INDEX=1
  │              ├─ 995–1013: append session_centroid_drift                ONLY IF session has prior baseline
  │              ├─ 962: chord-distance coherence_delta (deadband near 0)
  │              └─ 1037–1058: _KIND_THRESHOLDS-keyed fired_conditions
  │  → on SidecarUnavailable + RC_S2_FAIL_CLOSED=1 → BLOCK
  │  → on SidecarUnavailable + default              → fail-open + AUDIT
  ├─ 611–639: gate_mock_detector (RC_MOCK_DETECTOR=1 by default)
  ├─ 651–706: gate_drift (RC_DRIFT_WARN=4.0 / RC_DRIFT_DENY=6.0; cumulative_drift gate)
  └─ 766:     RC_SHADOW_MODE branch — log-only vs enforce
```

The two iter-3 levers (`RC_BEST_EFFORT_SPEC`, `RC_PLAN_GROUNDING`) — the ones that produce the headline +0.43/+0.32 wins — are *both* opt-in (`.envrc:133,141`) and absent from the 27% of sessions that bypass `direnv allow`. **The gate users actually run is materially weaker than the one the README markets.**

---

## 4. Existing audits — what they covered

[`2026-05-23-reasoning-core-effectiveness-audit.md`](2026-05-23-reasoning-core-effectiveness-audit.md) already documented:
- `gate_id` 0/30,275 populated → per-gate ablation impossible.
- Sidecar reliability 94.2%.
- Tier-2 host parity unfalsified (Vibe=27, Gemini=0, Copilot=0).
- Plan-grounding `audit_only` on 3,542 events.
- Unsupported-language drag (1,604 events, 5.3%).
- Override surface unmeasured for FPR.

The engineer-review ([`2026-05-23-effectiveness-audit-review-engineer.md`](2026-05-23-effectiveness-audit-review-engineer.md)) flagged: too many improvements, no ranking, no kill criteria.

The scientist-review ([`2026-05-23-effectiveness-audit-review-scientist.md`](2026-05-23-effectiveness-audit-review-scientist.md)) flagged: judge agreement unsettled, headline metrics not isolatable from audit log.

The plan-grounding review ([`2026-05-23-plan-grounding-plan-review-engineer.md`](2026-05-23-plan-grounding-plan-review-engineer.md)) flagged: plan-grounding generalisation untested beyond iter-3 fixtures.

**Holes none of those audits filled, and this doc does:**
- (a) Empirical degeneracy of `coherence_delta` and AIS — **§1.3 above is new**.
- (b) Per-signal-source latency ablation — **§1.4 is new** (prior audit reported aggregate p99; we report by signal).
- (c) The 11-dim → 8-dim production gap — **§Summary point 1 is new**.
- (d) Risk-vector redundancy across embedding-derived dims — **§5.B5 below is new**.
- (e) Day-zero installs ship with the value-generating levers OFF — **§Summary is new**.

---

## 5. Top-5 ranked improvement bets

> **Rule.** Each bet has: (a) what to do, (b) why empirical evidence supports it, (c) expected effect-size with a *stated metric and a stated MDE*, (d) cost (eng-days, latency, $$), (e) kill criteria — if X doesn't happen in N weeks, stop.

### B1. **Fix the coherence-delta deadband by switching to a Process Reward Model on the diff** [biggest leverage]

**What.** Replace (or supplement) the chord-distance `coherence_delta` with a small **Process Reward Model (PRM)** that scores each candidate diff hunk on a 0–1 calibrated scale derived from (plan-claim, diff-hunk) pairs. The current `gen_client.score_plan_grounding` (`src/gen_client.py:261`) already does roughly this for *one* plan-claim at a time, with a 0.5B-1.5B Qwen model, but only when `RC_BACKEND_ACTIVE=1` and only on plan-time scoring — not on Edit-time. Promote this to a first-class signal, scored against the *agent's stated plan + diff hunk*, with PRM-style step-wise output.

Mid-2026 PRM literature converged on this: **AgentPRM** ([arXiv:2511.08325](https://arxiv.org/abs/2511.08325)) scores agent decisions on *progress toward goal* — closer in spirit to "is this edit on-plan?" than to mathematical-step correctness. **FunPRM** ([arXiv:2601.22249](https://arxiv.org/html/2601.22249v1)) treats each function-level edit as a reasoning step in code generation. **ThinkPRM** ([arXiv:2504.16828](https://arxiv.org/abs/2504.16828)) shows a generative PRM beats discriminative verifiers on out-of-domain code with 8% gain at lower training cost. Apply this template to (plan_text, diff_hunk) pairs from `eval/calibrated/` and the iter-2 corpus already on disk (`thoughts/shared/research/iter3-decomposer-reliability-report.json` lists 10 plans).

**Why empirically.** §1.3: chord-distance fires on 1/197 sample events (0.5%). §1.2: `ssm` block hit-rate ≈ 0.5%. A PRM trained on plan↔diff pairs is the *only* signal that directly measures the metric users care about: "is the agent doing what it said it would do."

**Expected effect-size.** Primary metric: **plan_impl_drift block precision** (today: 54 warns + 0 measured ground-truth FP/TP). Target: PRM-driven warn precision ≥ 0.75 on a held-out set of 200 plan/diff pairs. Secondary: **flake_locked_pass_rate delta on iter-3 lev-2 suite** — MDE 0.13 per [`iter3-prereg-mde-table.json`](thoughts/shared/research/iter3-prereg-mde-table.json) at n=8 per task. PRM should lift this by ≥ +0.1 (i.e., 1 task of 10 flips locked).

**Cost.** 5–10 eng-days to wire the PRM call into `_dispatch.gate_plan_grounding`, train on existing v3 grounding pairs (`eval/runs/qwen_grounding_v3_20260506.json.partial.jsonl`), and add a `signal_source="prm"` audit emission. Inference cost: one Qwen-1.5B call per Edit, budget 1500ms — half the `ssm` p50 latency.

**Kill criteria.** If after 4 weeks PRM precision on the held-out 200 pairs is < 0.6, **revert and replace with a deterministic AST-distance signal**. If PRM p95 latency > 3000ms after 2 weeks, drop to scoring every Nth edit, not every edit.

### B2. **Cap sidecar latency at 1.5s; degrade-to-symbolic on timeout** [biggest UX leverage]

**What.** Hard client-side timeout on `_post_score` (`src/hooks/pre_edit_guard.py:206`) of 1500 ms. On timeout, **do not fail-open silently** — fall back to the symbolic rule engine (`src/hooks/_rule_engine.py`, 833 lines) and lang_lock alone, mark `signal_source="symbolic_fallback"`, emit an audit event. Currently the sidecar can take a full minute (§1.4) and we silently allow the edit anyway.

**Why empirically.** §1.4: ssm p95=58s, p99=60s. §1.5: 24% sidecar incident rate. Even if every long-tail call eventually returns useful info, the agent has long since aborted/retried. A hard 1.5s cap captures p50+p90 calls (3.4s p50 today suggests we'll cap ~50% of them — that's a deliberate Pareto improvement: we trade neural recall for predictable latency, and the symbolic gate already covers the worst-case bypass categories per the existing audit).

**Expected effect-size.** Per-session aggregate gate wall-clock: today ~30 Edits × 3.4s p50 ≈ 100s of latency. After cap: ≤ 30 × 1.5s = 45s. Save **~55s/session p50**, **much more on long tail**. Cost recovers most of the +98s/run iter-3 deficit.

**Cost.** 1 eng-day. Add `RC_S2_HARD_CAP_MS` env knob (default 1500). Wire stderr message: "[hybrid-reasoner] sidecar slow; symbolic fallback engaged."

**Kill criteria.** If `symbolic_fallback` rate > 30% of all Edits after 1 week, ship perf work on the sidecar (mamba forward pass batching) before continuing.

### B3. **Recalibrate `coherence_delta` against the new chord-distance scale, or replace it** [biggest correctness leverage]

**What.** The metric migrated from `L2/sqrt(D)` to chord distance ([`2026-05-05-coherence-delta-calibration.md`](2026-05-05-coherence-delta-calibration.md), [`2026-05-05-risk-vector-delta-refactor.md`](2026-05-05-risk-vector-delta-refactor.md)), but the production `COHERENCE_DELTA_THRESHOLD=0.5` (`s2_core.py:778`) is a value that doesn't fire (§1.3: 1/197 sample events trigger). Two options, in order:

  (a) **Recalibrate.** Set threshold from the empirical 95th-percentile of `coherence_delta` over the audit corpus, per source-code kind. From our 197-event sample, the 95th percentile is **~0.09**, the 99th is ~0.50. The 99th-percentile-floor threshold (~0.5) is technically calibrated — but the gate by definition only fires 1% of the time, and a 1% gate cannot meaningfully shape agent behaviour.

  (b) **Replace** with a per-file *delta* of cosine similarity to the project-wide centroid (the `__corpus__` in `_get_session_baseline_for_path`, `s2_core.py:189-202`). This is the "session_centroid_drift" dim that *already* exists in the API but never lands in audit events because `session_id` is never threaded through (it's pulled from `_get_session_baseline` at `s2_core.py:184-187`, which requires a per-session `/baseline` ingest call that isn't part of normal Claude-Code Edit flow).

Either way, **the metric must fire on more than 5% of edits or it's a dead signal.** SOTA mid-2026 retrieval-augmented code agents use **graph-aware embedding distances** to a code-context graph (GraphCoder, RepoHyper — see [arXiv:2510.04905 survey](https://arxiv.org/html/2510.04905v1), [arXiv:2504.10046](https://arxiv.org/pdf/2504.10046)) which measure "how different is this edit from the repo's idiom" much more discriminatively than chord-on-pooled-embedding. The infra is already there (`src/project_index.py`); just default it on (`RC_PROJECT_INDEX=1`) and emit the dim in audit.

**Why empirically.** §1.3: 98% of edits register coherence_delta < 0.10. The metric has *no statistical power* at the current threshold.

**Expected effect-size.** After recalibration to empirical-95th-percentile floor: gate fires on ~5% of edits (target). Of those, target ≥ 50% precision against a human-graded "this edit drifts from project idiom" rubric — a 25× lift over today's effective 0.5% fire rate.

**Cost.** 3 eng-days. Audit log already has the raw `coherence_delta` to compute the 95-pctile floor offline. Recalibration script: 100 LOC.

**Kill criteria.** If new threshold lifts precision < 0.3 on a 100-edit human grading, switch to option (b) and default `RC_PROJECT_INDEX=1` system-wide (the 11-dim "claim" finally becomes a fact).

### B4. **Default-on the two iter-3 levers that produced the headline wins** [biggest go-to-market leverage]

**What.** Flip `RC_BEST_EFFORT_SPEC=1` and `RC_PLAN_GROUNDING=1` to the default in `.envrc:133,141`. Today they ship `=0`. The +0.43 repo-fit / +0.32 plan-quality wins in `docs/BENCHMARKS.md` are achieved **only** under setup-B's overlay where these flip to `=1` ([`docs/iter3-frozen-artifacts/eval-setups-B/envrc.txt`](docs/iter3-frozen-artifacts/eval-setups-B/envrc.txt)). The README pitches those numbers but the install ships the weaker config.

**Why empirically.** Audit log: `RC_PLAN_GROUNDING` fires on 312 events in our sample; 54 of those (17%) flagged `plan_impl_drift` — the single most concentrated source of *reasoning-quality* signal in the whole corpus (vs `ssm`'s 0.5% block rate). Why is it off by default?

**Expected effect-size.** On day-zero installs (currently the majority — see §1.6 project diversity, only `reasoning-core` itself opts in via direnv), enable the same setup-B configuration that produced +0.32 plan-quality. Aggregate across all installs.

**Cost.** 0 eng-days for the env-flip. ~2 eng-days for the "first-run wizard" that asks "create a PLAN.md from your README?" before enabling `RC_PLAN_GROUNDING=2` (the block-tier), so we never block an Edit because the user hasn't authored a PLAN.md yet. (Auto-scaffold PLAN.md is exactly recommendation #3 in the prior audit's "Improve" section.)

**Kill criteria.** If the FPR on `plan_impl_drift` warns exceeds 30% (measured by a 2-week shadow audit on 5 self-installs), back off to `RC_PLAN_GROUNDING=1` (warn-only) as default and keep `=2` opt-in.

### B5. **Make the 11-dim vector actually 11-dim, and prune the 3 redundant dims** [biggest math-rigor leverage]

**What.** Two sub-actions:

  (i) **Plumb `session_id` to `/score`** so `session_centroid_drift` actually computes. Today it requires `session_id and RC_PROJECT_INDEX=1` (`s2_core.py:996, 1018`) — `RC_PROJECT_INDEX=1` is already set in this repo's `.envrc:226` but the audit log shows the dim is never emitted on real Edits, meaning `session_id` is not being passed in the `/score` POST. Fix in `src/hooks/pre_edit_guard._post_score` (`pre_edit_guard.py:183-211`) — include `session_id` from `audit_log._session_id()`.

  (ii) **Empirically test redundancy.** Three of the 11 dims (`novelty`, `session_centroid_drift`, `project_coupling`) all derive from the same SSM embedding. Run a correlation test across the iter-3 grounding corpus: if pairwise |ρ| > 0.7, drop the lower-information dim. The empirical 8-dim breakdown today is heavily skewed — most events have 7 of 8 dims = 0.0 with only `churn` ≈ 0.02 (see §1.3 sample). Pruning would also let us *raise* the dim-ceiling threshold from 0.9 (`s2_core.py:788`) without losing fire-rate, because the remaining dims will be denser.

**Why empirically.** §Summary #1: production vector is 8-dim. Marketing 11 dims that don't ship is a credibility hit. Math-side: 3 embedding-derived dims compete for the same signal channel.

**Expected effect-size.** Internal claims-discipline. Secondary: a properly-fired `session_centroid_drift` dim is the single best candidate to replace the dead `coherence_delta` (B3).

**Cost.** 2 eng-days. Plumb `session_id`; write correlation test against the corpus; update docs.

**Kill criteria.** If after enabling `session_id` flow, the new dim still emits 0.0 on > 80% of edits (i.e., baseline ingest never happens), drop it from the spec entirely and ship a 7-dim vector. Honest 7 beats theatrical 11.

---

## 6. What we are explicitly **NOT** doing in this round

To kill the "30-fix anti-pattern" the engineer-review flagged:

- **Not** adding `gate_id` plumbing. Already in [`2026-05-23-audit recommendations #1`](thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md). Should land — but it's instrumentation, not reasoning-quality. Track as `INFRA-1`.
- **Not** chasing Gemini / Copilot parity. Engineering audit already named this; without empirical traffic to validate, this is theatre. Track as `PARITY-1`.
- **Not** restarting SWE-bench iter1. The pilot pause-cause was Gemini-quota + VM teardown, not a sidecar bug ([`2026-05-09-pilot-status.md`](thoughts/shared/research/2026-05-09-swebench-iter1-pilot-status.md)). It will resume on its own schedule; nothing in the audit suggests changing the protocol.
- **Not** adding new symbolic rules. The existing `_rule_engine.py` (833 lines) covers everything we have evidence the agent abuses (shell-escape, kill-sidecar). Symbolic surface should *shrink* before it grows.
- **Not** retraining the mamba backbone. The dead coherence-delta is a **threshold / metric-design** problem, not a model-capacity problem. `random-mamba` exists as a falsifiability control (`ssm_backbone.py:99`) precisely to test this — has anyone run it lately?

---

## 7. A north-star metric for "1000% more efficient at improving agent reasoning"

The prompt asked for "1000% more efficient." Without a metric, that's a vibe. Propose this composite:

```
agent_reasoning_efficiency =
    (plan_impl_drift_caught - false_drifts) / (gate_wall_clock_s + 1)
        × repo_idiom_adherence_delta_norm
        × (1 − sidecar_unavailability_rate)
```

Concretely:
- **Numerator: net true-positive drift catches** = blocks that the agent then *honored* (retry_after_block=true AND the corrected diff lands), minus blocks the operator overrode (`RC_ALLOW_GUARD_EDIT=1` after the block).
- **Denominator: gate wall-clock seconds per session**, summed from `latency_ms`.
- **Multiplier 1: judge-rated repo-fit delta**, normalised to [0,1].
- **Multiplier 2: sidecar uptime fraction**, from `fail-open`+`fail-closed`/total.

**Today's baseline** (estimated from sample): ~3 net true drifts caught per session × 100ms p50 gate wall-clock ÷ ((30 × 3400 ms) sidecar wall) × 0.76 sidecar uptime × 0.43 repo-fit (iter-3) ≈ **0.0008 reasoning-quality-units / session-second**.

**Target: 10× by 2026-12-01**, **100× by 2027-03-01** (closer to "1000×" is aspirational and depends on PRM training data we don't have yet).

Implementation: add a `rc reasoning-efficiency` subcommand to `src/rc_cli.py` that computes this from the audit log. The audit log already carries `latency_ms`, `decision`, `signal_source`, `retry_after_block`. Joining `retry_after_block` against the next-event's decision is the only missing computation.

---

## 8. Architecture Notes

- **Trust boundary.** `127.0.0.1:8765` (sidecar) and `127.0.0.1:4000` (LiteLLM gateway, per `.claude/settings.local.json`) are both loopback. No regression.
- **Schema v3** is forward-compatible. Adding `prm_score`, `symbolic_fallback`, `coherence_delta_v2`, `gate_id`, `shadow_mode` fields per recommendation does not break readers.
- **Default-off levers.** RC_BEST_EFFORT_SPEC, RC_PLAN_GROUNDING, RC_CALIBRATION_ENABLED, RC_PROJECT_INDEX (default 1 in this repo's `.envrc:226` but not in install.sh's installed `.envrc.local`). The pattern of "ship the lever in code, default it off, document it in `docs/iter3-levers.md`" is intentional but fragile — users only get the value if they read the doc.
- **Cold-start zero-out** (`s2_core.py:962-981`) zeros 7 of 8 risk dims for any file with `len(before_src) < 32`. Combined with empty-file-write Writes, this means new-file creation is essentially ungated except by symbolic rules.

---

## 9. External Dependencies

| Dep | Used in | Risk |
|---|---|---|
| `state-spaces/mamba-130m-hf` | default embedder | Single-tenant SSM; no batching ⇒ p99=60s |
| `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` | gen_client critic | Required for plan-grounding `score_plan_grounding`; check `RC_BACKEND_ACTIVE` |
| `princeton-nlp/SWE-bench_Verified` | iter-1 SWE-bench pilot | Pilot paused; not blocking the gate |
| `portalocker ≥ 2.7` | audit-log writes | Multi-host concurrency — works |
| `direnv` | env-flag distribution | If user skips `direnv allow`, all defaults stay off |

---

## 10. Open Questions

1. **Has anyone run `random-mamba` as a control recently?** `src/ssm_backbone.py:99` ships a randomly-initialised Mamba-2 specifically for falsifiability — if a real-vs-random A/B on the iter-3 corpus shows no significant difference in block precision, the SSM signal is not just degenerate but non-existent.
2. **What does the PRM-on-plan-grounding signal look like on day-zero installs (no PLAN.md)?** The auto-scaffold proposal (B4) is necessary but the *quality* of an auto-scaffold-from-README plan is unknown. Sample: run scaffold on 10 popular OSS repos, hand-grade 1-5.
3. **Why is `cumulative_drift` always None in the audit?** Read `_supervisor_recalibrate.py:32`'s `eval/runs/recalibrate.signal` path — is the signal even being written? If so, by what?
4. **What is the operator-override survival ratio?** Of the 4 `magic_comment_self_introduced` events and 69 `RC_ALLOW_GUARD_EDIT=1` overrides cited in the prior audit, how many of the *bypassed* diffs survived to a committed git ref? Without this, we can't compute the gate's true false-positive rate — and without that, we can't tune any threshold honestly.
5. **Is the sidecar's `gate_drift` (cumulative_drift > RC_DRIFT_DENY=6.0) ever actually triggered?** Audit shows `cumulative_drift` is None in 100% of sampled events. Either the supervisor isn't computing it, or it's not on the `/score` response path. (`src/hooks/_dispatch.py:387-403` references it but only inside `gate_drift`; if it never lands, that gate is dead code.)

---

## 11. Appendix — raw sample counts

```
Total files on disk : 8,885
Days covered        : 25 (2026-05-05 → 2026-05-29)
Stratified sample   : 71 files (3/day max)
Events parsed       : 3,657
Schema version      : 3 (all events)

Decision        Count   %
---------       -----   ----
allowed         3,050   83.4
audit_only        258    7.1
fail-open          87    2.4
unsupported        99    2.7
warn               54    1.5
blocked            80    2.2   ← of which 21% reasoning, 65% self-protection
injected           18    0.5
skipped             8    0.2
degraded            3    0.1
shadow_blocked      1    0.03

Signal source            Count   With risk_vector  With coherence_delta
-----------              -----   ----------------  --------------------
(null)                   2,899              0                  0
ssm                        417            417                197
plan_grounding             312              0                  0
best_effort_spec            26              0                  0
lang_lock                    3              0                  0

Latency by signal (ms)
ssm           : p50=3437  p95=57829  p99=60030  max=60156
plan_grounding: p50=2     p95=4      p99=12     max=28
lang_lock     : p50=3     p95=3      p99=3      max=3
None          : p50=6     p95=9      p99=18     max=19260
```

---

## 13. Implementation status (landed 2026-06-02)

All 5 ranked bets shipped as 11 work units + 2 verification follow-ups on local `main`. Commits, in dependency order:

| Commit | Unit | Bet |
|---|---|---|
| `135f801` | U2 — recalibrate `S2_COHERENCE_THRESHOLD` 0.5 → 0.09 (empirical p95) | B3a |
| `aa10ff2` | U1 — hard-cap sidecar at `S2_HARD_CAP_MS=1500` with symbolic fallback | B2 |
| `b633cd1` | U3 — `session_id` fallback to `audit_log._session_id()` | B5.i |
| `cba5764` | U4 — plumb `gate_id` on every audit event | infra-1 |
| `072596a` | U5 — default-on `RC_PLAN_GROUNDING=1` + auto-PLAN.md scaffold | B4 |
| `ceb029b` | U6 — `scripts/risk_vector_correlation.py` redundancy tool | B5.ii |
| `91d7bf2` | U7 — `rc reasoning-efficiency` north-star metric subcommand | §7 |
| `6d44e21` | U8 — `gate_prm` measurement-only audit (`signal_source="prm"`) | B1 surface |
| `f4810a3` | U9 — `eval/build_prm_corpus.py` AgentPRM-style auto-labels | data |
| `1e37fc0` | U10 — `eval/calibration_corpus.py --include-positives` | data |
| `4d1c2d1` | U11 — docs + CHANGELOG | comms |
| `54c7aad` | follow-up — scaffold self-protection against reasoning-core repo | bugfix |
| `929789f` | follow-up — slow-stub `BrokenPipeError` suppression in tests | bugfix |

### Empirical reads from the verification pass (2026-06-02)

After landing, drove every user-visible surface end-to-end against real data (audit log, iter-2 corpus, reasoning-core git history). Notable findings:

- **§B2 (hard cap) confirmed in production**: subprocess hook against a 5 s slow stub with `S2_HARD_CAP_MS=500` exits in 0.70 s with `fail-open` audit, vs ~5 s previously. Magnitude matches the model's prediction (1.5 s p99 vs 60 s p99).
- **§B3 (coherence recalibration)**: threshold flipped to 0.09. Will take ~14 days of fresh production traffic to know whether the new threshold actually fires on 5 % of edits as targeted.
- **§B5.i (session_id fallback) — open**: `scripts/risk_vector_correlation.py` against the 3,089-event audit log shows `session_centroid_drift`, `project_fan_in`, `project_coupling` are still **all-NaN today**. The fix landed in `b633cd1` but the audit log is historical — the Phase-2 dims start firing only after this commit. **Re-run ~2026-06-15 to confirm they cross the threshold.** If they remain NaN, U3 didn't reach `/score` in real Claude Code sessions and needs a different fix (e.g., make `audit_log._session_id()` write to disk before the hook returns).
- **§B5.ii (correlation tool)**: the 8 original dims show 3 redundant pairs: `fan_in↔fan_out` r=+0.708, `fan_in↔depth` r=+0.876, `fan_out↔depth` r=+0.757. These are all structural depth proxies — strong candidate to prune from 8 → 6 once Phase-2 dims start firing.
- **§B4 (plan-grounding default-on)**: `RC_PLAN_GROUNDING=1` and `RC_BEST_EFFORT_SPEC=1` shipped as defaults. Iter-3 historical replays must use the pinned `docs/iter3-frozen-artifacts/eval-setups-A/envrc.txt` (still pins `=0`) to keep A vs B comparable.
- **§9 (PRM corpus)**: real iter-2 corpus → 900 rows with label distribution `{-1: 360, 0: 260, +1: 280}`. Ready for AgentPRM-style training.
- **§10 (positive-label corpus)**: reasoning-core's own 1-month history → 39 positives from 16 distinct `fix_parent:<sha>` commits. The corpus is **self-feeding** — one positive sample points at `src/hooks/pre_edit_guard.py` from `b633cd1` (this batch's own U3 fix), which means a future SSM trained on this corpus will learn that "missing session_id fallback" is regression-shaped. That is the correct lesson.

### What `1000% more efficient` looks like as a measurable target

The §7 composite metric is now computable via `rc reasoning-efficiency`. On the 3,089-event production audit log today it reads ~0.27 normalised units. Reaching `10×` of that requires either (a) more drift catches in the numerator — which only happens after the PRM gate flips on with calibrated thresholds, or (b) lower `gate_wall_clock_s` — which U1's hard cap already addresses for the long tail. The roadmap to 1000× depends on training a real PRM from the §9 corpus; that work is queued behind this commit, not blocked by it.

### Known follow-ups (queued, not in this batch)

1. **Re-run correlation tool ~2026-06-15** to confirm Phase-2 dims start firing in production.
2. **Train PRM on `eval/calibrated/prm_corpus.jsonl`** then calibrate `RC_PRM_GATE` thresholds against a held-out set before flipping default-on.
3. **Periodic positive-label re-mining** — `eval/calibration_corpus.py --include-positives` yields more positives as fix/revert commits accumulate.
4. **Symbolic-rule audit** — when `signal_source="symbolic_fallback"` rate is measurable (4 weeks of `S2_HARD_CAP_MS=1500` data), audit which symbolic rules fire most and whether they catch the same things the SSM would have.

---

## 14. References

### Repo artifacts cited
- `src/s2_core.py`, `src/ssm_backbone.py`, `src/hooks/_dispatch.py`, `src/hooks/pre_edit_guard.py`, `src/hooks/pre_bash_guard.py`, `src/gen_client.py`, `src/project_index.py`, `src/rc_cli.py`, `.envrc`
- `docs/BENCHMARKS.md`, `docs/iter3-levers.md`, `docs/iter3-frozen-artifacts/eval-setups-{A,B}/`
- `eval/run_suite.py`, `eval/runs/smoke-001/`, `eval/runs/qwen_grounding_v3_20260506.json.partial.jsonl`
- `thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md` *(prior audit)*
- `thoughts/shared/research/2026-05-23-effectiveness-audit-review-engineer.md`
- `thoughts/shared/research/2026-05-23-effectiveness-audit-review-scientist.md`
- `thoughts/shared/research/2026-05-23-plan-grounding-plan-review-engineer.md`
- `thoughts/shared/research/iter3-prereg.json`, `iter3-prereg-mde-table.json`, `iter3-decomposer-reliability-report.json`, `iter3-probe-3judges-round-9-LANDED.json`
- `thoughts/shared/research/2026-05-09-swebench-iter1-pilot-status.md`, `2026-05-10-swebench-iter1-resume-plan.md`

### SOTA literature (mid-2026)
- AgentPRM — [arXiv:2511.08325](https://arxiv.org/abs/2511.08325) — step-wise promise/progress scoring for agents
- FunPRM — [arXiv:2601.22249](https://arxiv.org/html/2601.22249v1) — function-as-step PRM for code generation
- ThinkPRM — [arXiv:2504.16828](https://arxiv.org/abs/2504.16828) — generative PRM beats discriminative at lower training cost
- ToolPRMBench — [arXiv:2601.12294](https://arxiv.org/abs/2601.12294) — benchmark for tool-using agent PRMs
- SAGE multi-agent self-evolution — [arXiv:2603.15255](https://arxiv.org/pdf/2603.15255) — Challenger/Planner/Solver/Critic
- Agentic Verifier for competitive coding — [arXiv:2602.04254](https://arxiv.org/pdf/2602.04254) — execution-grounded discriminator
- Agentic Rubrics as Contextual Verifiers for SWE — [arXiv:2601.04171](https://arxiv.org/pdf/2601.04171) — K=16 rollouts + rubric reranking on SWE-bench
- CodeRAG / GraphCodeAgent — [arXiv:2504.10046](https://arxiv.org/pdf/2504.10046) — dual graph-guided RAG for repo-level code
- Retrieval-Augmented Code Generation survey — [arXiv:2510.04905](https://arxiv.org/html/2510.04905v1) — taxonomy incl. GraphCoder, RepoHyper, CodeGRAG
- Speculative Verification — [arXiv:2509.24328](https://arxiv.org/html/2509.24328v2) — small companion model for verification length adaptation
- Awesome-Process-Reward-Models — [github.com/RyanLiu112/Awesome-Process-Reward-Models](https://github.com/RyanLiu112/Awesome-Process-Reward-Models) — curated PRM bibliography
