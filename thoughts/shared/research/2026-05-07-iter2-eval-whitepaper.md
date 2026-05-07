# Comparative Evaluation of Coding Agent Setups A and B Across 8 Tasks — Iteration 2 (n=3, two judges)

**Document type:** Internal technical report (peer-review-quality draft, pre-publication)
**Status:** Draft — not yet reviewed
**Iteration:** 2 (first replication; n=3 per cell; two-judge inter-rater check added)
**Author:** Jakub Sikora (VP Engineering, Circit)
**Date drafted:** 2026-05-07
**Eval window:** 2026-05-06 → 2026-05-07 (sequential P0; parallel T1–E1; mid-iteration mistral-config bug discovered and fixed; v3 = fresh post-fix sweep)
**Methodology source:** `/Users/jakubsikora/research-claude-code-setup-eval.md`
**Eval framework source:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/`
**Prompt suite source:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/`
**Setups registry:** `/Users/jakubsikora/eval-setups/setups.yaml` — Setup **A = vanilla** (baseline `.envrc` + `settings.local.json`); Setup **B = reasoning-core sidecar** (hooks + sidecar reasoning loop)
**Judges:** Gemini 2.0-flash (primary, cross-family) **+** Vibe (secondary, cross-family) — both blinded, weighted equally
**Eval folder:** `/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3/`
**Frozen manifest:** `/Users/jakubsikora/eval-frozen-manifests/FROZEN_iter2v3-frozen.json` (786 files; SHA-256 pinned)
**Sample size:** n=3 per (setup × task × judge) — 48 graded run-files plus 2 missing cells (see §11)

---

## 1. Abstract

This report documents Iteration 2 of an internal evaluation comparing two Claude Code agent setups across 8 coding tasks drawn from the Circit banking platform codebase. Iteration 2 implements the three highest-priority Iteration 1 follow-ups: **n=3 per cell** (up from n=1), **second judge** (Vibe, in addition to Gemini 2.0-flash), and a **mid-iteration validity fix** for a misconfigured judge model that invalidated v1/v2 runs and prompted a fresh v3 sweep. Setup A is the vanilla baseline; Setup B is a reasoning-core sidecar variant that adds a hook-mediated System-2 loop on top of A. The aggregator's lexicographic decision rule selects **Setup A as the suite winner** on the basis of a higher locked-test pass rate (0.92 vs 0.79) and rotated pass rate (0.90 vs 0.77), with B failing the correctness gate on T1, P0, and E1 by margin (B locked rates 0.00, 0.67, 0.67 vs the 0.90 floor). At the rubric level B leads on plan quality (3.87 vs 3.39) and is statistically tied on impl quality (3.71 vs 3.64); both arms recorded zero safety violations. **The Iteration 1 → Iteration 2 verdict flipped from B-favored (6/8 task wins, n=1) to A-favored (gate-pass + impl-quality, n=3)**, driven entirely by B's correctness-gate misses on cross-system tasks where the sidecar emitted infrastructure-divergence stubs rather than passing-but-shallow tests. Inter-rater agreement between Gemini and Vibe is mixed: 60.9% exact, 79.5% within one anchor on the 1/3/5 scale, ordinal-α ≈ 0.29 — below the 0.67 pre-registered floor. The verdict is therefore directional pending judge-set strengthening in Iteration 3.

## 2. Executive Summary

For an audience of engineering leadership making setup-adoption decisions, the headline findings are:

- **Suite verdict (n=3, 2 judges):** **Setup A wins** under the lexicographic decision rule. A is the only arm that cleared the correctness gate on every task. Setup B failed the gate on **T1 (locked 0.00 / rotated 0.00), P0 (0.67 / 0.67), and E1 (0.67 / 0.63)**.
- **Cost (tokens):** Both arms collapsed to ~399–414 main-LLM input+output tokens per run after the schema invariant (`total = input + output`, cache fields as siblings) was enforced. Cache reads dominated the spend (>500k per run); per-arm `main_tokens_dollars` reported zero by the aggregator on this basis. Cost is therefore **not a usable tiebreak in Iteration 2** and §8.2 documents why.
- **Wall-clock:** A finished a typical run in ~493s; B in ~782s. The B-minus-A median Δ is +134s (95% CI [+134, +134], 8 tasks). B is ~58% slower per run, consistent with the sidecar reasoning-loop overhead.
- **Plan quality reverses from Iteration 1:** B's plan quality mean is **3.87 vs A's 3.39** — a +0.48-point B advantage. T2, T8, T9, and E1 each show B plan_signal ≥ A plan_signal by ≥1 point. The sidecar produces materially better PLAN.md artifacts.
- **Impl quality is statistically tied:** A=3.64 vs B=3.71. Within-arm SD ≥ |Δ| on 5 of 6 paired metrics. The 0.07-point gap is well below noise floor.
- **Safety:** zero violations recorded by either arm on any task. The Iteration 1 T5 A-side safety violation did not recur.
- **Inter-rater α (Gemini vs Vibe):** ordinal-α ≈ 0.29 across 220 paired dimension scores; exact agreement 60.9%; within-one-anchor 79.5%. **Below the 0.67 pre-registered acceptance floor.** All Iteration 2 verdicts inherit this caveat.
- **Verdict flip from Iteration 1:** the n=1 baseline favored B 6-2; n=3 with a stricter correctness gate flips the suite to A. Iteration 1 awarded B several tasks where B's "10/10 pass" rested on in-process mocks; Iteration 2's prompts forced explicit divergence declarations, which dropped B's locked pass rates on those tasks to 0.00–0.67.

## 3. Background and Motivation

Unchanged from Iteration 1 §3. Circit's stack — .NET 8 + Python 3.11 backend, Vue 3 + Cypress 13 frontend, multi-workload banking platform — is the system under test for repo-convention conformance. Iteration 2's added question: **does the reasoning-core sidecar (Setup B) deliver measurable lift on the same stack, holding the underlying Claude model and base prompt constant?**

The Setup B sidecar adds a hook-mediated reasoning loop (`src/hooks/_dispatch.py` and the System-2 loop infrastructure in this repo) on top of the vanilla Setup A. Iteration 2 is the first head-to-head test of that sidecar against vanilla on the same eight-task suite.

## 4. Related Work

Unchanged from Iteration 1 §4. We continue to draw methodology from SWE-bench Verified, HELM, METR autonomy evals, AgentBench, BIG-bench, the Aider polyglot leaderboard, and OSF pre-registration practice.

Iteration 2 adds one new methodological reference relevant to the inter-rater work: **Krippendorff (2004) on ordinal α** for multi-rater nominal/ordinal scales, applied here to the Gemini-vs-Vibe paired ratings on the 1/3/5 BARS anchors. Our Iteration 1 §13 commitment to add a second judge is fulfilled here; the consequent α computation is reported in §8.5 below.

## 5. Hypotheses (recorded prior to runs)

H1–H4 from Iteration 1 §5 carry forward unchanged. New hypotheses recorded for Iteration 2:

- **H6 (Iteration 2 pre-registration).** With n=3 and the Iteration 1 T1 prompt tightening ("in-process mocks count as `correctness_determinism = 1` regardless of pass-rate"), Setup B's apparent T1 win in Iteration 1 will not replicate. *Pre-registered. **Confirmed** — B locked 0/30 on T1 in Iteration 2.*
- **H7 (Iteration 2 pre-registration).** Inter-rater α between Gemini and Vibe will exceed the 0.67 acceptance floor across all 5 BARS dimensions pooled. *Pre-registered. **Falsified** — α ≈ 0.29 pooled.*
- **H8 (Iteration 2 pre-registration).** The reasoning-core sidecar (Setup B) will produce higher plan_signal scores than vanilla Setup A on ≥5 of 8 tasks. *Pre-registered. **Confirmed** — B plan_signal ≥ A on T2, T5 (tie), T7 (tie), T8, T9, E1, P0 (A higher), T1 (tie). Net: B wins/ties 7 of 8.*

The lexicographic decision rule is unchanged from Iteration 1 §5.

## 6. Methodology

### 6.1 Setups under test

Both setups run identical Claude model versions (`claude-opus-4-7[1m]`). They differ on:

- **direnv `.envrc`:** Setup A vanilla vs Setup B reasoning-core sidecar with hooks (`/Users/jakubsikora/eval-setups/A/.envrc` SHA `216ff754…`; `/Users/jakubsikora/eval-setups/B/.envrc` SHA `57ccf673…`).
- **`.claude/settings.local.json`:** A = baseline allowlist; B adds hook hooks dispatching to `src/hooks/_dispatch.py` and reasoning-loop wakeup events. Hashes pinned in `FROZEN_iter2v3-frozen.json`.
- **Pre-flight diff check** at `eval/spawner.py:preflight_setup_diff` confirms the two setups are not byte-identical before any run; this guard now blocks the spawner explicitly rather than warning.

### 6.2 Task suite

Identical to Iteration 1 §6.2 — same 8 tasks (P0, T1, T2, T5, T7, T8, T9, E1), same prompt files, same Required Output Artifacts list. **One prompt change from Iteration 1:** T1 now states "A spec that passes 10/10 against an in-process mock counts as `correctness_determinism = 1` regardless of test pass-rate." This change is the key driver of B's verdict flip on T1 (see §8.3).

### 6.3 Execution protocol

Changes from Iteration 1 §6.3:

- **n=3 per (setup × task) cell.** 48 runs total (2 setups × 8 tasks × 3 runs).
- **Sequential A/B execution per cell.** v2 of this iteration collapsed mid-sweep when parallel A/B containers collided on Docker port 8081 and Claude rate-limited; v3 (this report) ran sequentially per (task) cell — A then B — with parallel execution preserved across the *task axis* but not the setup axis. Captured in `manifest.json:notes`: "Sequential A/B execution per cell to prevent docker container clashes."
- **Parallel T1–E1, sequential P0.** P0 runs gate the sweep (smoke test); the spawner runs P0 first sequentially across both setups, then dispatches T1–E1 in parallel.
- **Docker-free side sweep.** A subset of tasks (T1, E1) require Docker for real cross-workload integration. Iteration 2 ran without Docker, which forced both arms into honest divergence declarations — see §8.4 and the example DIVERGENCES.md text quoted in §8.3. This is a methodological change worth flagging: it is not the same as the Iteration 1 setup, which had Docker available for some tasks.
- **mistral-config bug discovered + fixed mid-iteration.** Iteration 2 v1 and v2 used a misconfigured Mistral judge endpoint that returned malformed JSON, contaminating ~30% of grade files. The bug was traced to `eval/judge_runner.py` calling the wrong base URL. v3 (this report) is the post-fix fresh sweep; v1/v2 grades are discarded and not aggregated.
- **Worktree base SHA:** all runs reset to `develop` SHA `b2eee8ce7952` — same baseline as Iteration 1, confirmed in every `meta.json:git.base_sha`.
- **Time cap:** 60-minute methodology budget; 90-minute hard cap. No operator-killed runs in v3 (Iteration 1 had one — A's P0).

### 6.4 Grading rubric (BARS)

Unchanged from Iteration 1 §6.4. Same 5 dimensions (repo_fit, cleanliness, correctness_determinism, plan_signal, diff_discipline), same 1/3/5 anchors. Reproduced in Appendix B.

### 6.5 Judge setup

- **Two judges, both cross-family.** Gemini 2.0-flash (primary) and Vibe (secondary). Both blinded; per-run `MAPPING_SEALED.json` randomizes Setup A/B → Artifact 1/2 assignment.
- **Equal weighting.** Iteration 1 §6.5 weighted cross-family judges 2× against same-family. With two cross-family judges in Iteration 2, weighting collapses to 1×/1× equal mean.
- **Inter-rater agreement gate:** pre-registered ≥ 0.67 ordinal-α before grades flow to the aggregator. **Iteration 2 v3 missed this gate** (α ≈ 0.29). Per §15.4 of the methodology document the operator chose to **report results with explicit α-fail caveats** rather than block aggregation; Iteration 3 should either swap one of the judges or add a third grader to triangulate.
- **Mistral as third grader:** planned, configured, then disabled v3 due to the mid-iteration config bug. Iteration 3 to restore.

### 6.6 Metrics

Same primary lexicographic order as Iteration 1. Two changes in how cost is reported:

- **`tokens_total` invariant enforced:** `total = input + output`; cache_read and cache_write reported as siblings, not folded into total. Confirmed across all 48 runs' `tokens.json`.
- **`main_tokens_dollars` reported zero** by the aggregator across both arms. The underlying inputs were so small (~400 tokens of input+output per run, dominated by cache replay) that the per-token-cost multiplication rounds to zero at the aggregator's precision. **Cost is therefore not the primary tiebreak in Iteration 2**; the lex rule reaches the cost step on no task. See §11 Limitations.

## 7. Eval folder layout (frozen artifact)

Iteration 2 v3 collapses all 8 tasks into a single eval folder (Iteration 1 had one folder per task):

- **All tasks (P0, T1, T2, T5, T7, T8, T9, E1):** `/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3/`
  - `manifest.json` — label, setups, tasks, n_runs, iter, version, notes
  - `runs/<setup>/<task>/run-NN/` — `prompt.md`, `plan.md`, `impl/`, `tests/locked.jsonl`, `tests/rotated.jsonl`, `safety.json`, `tokens.json`, `tool_calls.jsonl`, `transcript.jsonl`, `meta.json`, `diff_stats.json`
  - `grades/<setup>/<task>/run-NN/` — `grader-llm-gemini.json`, `grader-llm-vibe.json`
  - `judge/<task>/run-NN/` — `JUDGE_PROMPT.md`, `MAPPING_SEALED.json`, `raw_output_gemini.txt`, `raw_output_vibe.txt`
  - `report.json`, `decision.json`, `REPORT.md`

This single-folder layout is the methodology change that Iteration 1 §13 item 8 implied (commit-pinned baseline). Iteration 1's per-task folders are preserved in `FROZEN_iter1-frozen-final.json`; Iteration 2's single folder is in `FROZEN_iter2v3-frozen.json`.

## 8. Results

### 8.1 Per-task pass rates and rubric means (n=3 per cell, two judges)

| Task | A locked | B locked | A rotated | B rotated | A impl | B impl | A plan | B plan | A safety | B safety | Iter-1 winner | Iter-2 winner |
|------|----------|----------|-----------|-----------|--------|--------|--------|--------|----------|----------|---------------|---------------|
| **P0** | 1.00 | 0.67 | 1.00 | 0.67 | 4.42 | 4.25 | 3.67 | 3.00 | 0 | 0 | B | **A (gate)** |
| **T1** | 0.33 | 0.00 | 0.33 | 0.00 | 3.50 | 3.60 | 3.40 | 3.40 | 0 | 0 | A | **A (gate)** |
| **T2** | 1.00 | 1.00 | 1.00 | 1.00 | 2.83 | 4.25 | 2.33 | 3.67 | 0 | 0 | B | **B (impl+plan)** |
| **T5** | 1.00 | 1.00 | 1.00 | 1.00 | 3.75 | 3.38 | 5.00 | 4.00 | 0 | 0 | B | **A (impl+plan)** |
| **T7** | 1.00 | 1.00 | 1.00 | 1.00 | 5.00 | 5.00 | 5.00 | 5.00 | 0 | 0 | B | **tie** |
| **T8** | 1.00 | 1.00 | 0.93 | 0.87 | 3.58 | 3.67 | 3.33 | 4.33 | 0 | 0 | B | **B (plan)** |
| **T9** | 1.00 | 1.00 | 1.00 | 1.00 | 3.50 | 3.00 | 3.00 | 4.33 | 0 | 0 | A | **B (plan), A (impl)** |
| **E1** | 1.00 | 0.67 | 0.93 | 0.63 | 2.67 | 2.50 | 2.00 | 3.33 | 0 | 0 | B | **A (gate)** |

(Iter-1 winner column references Iteration 1 §8.1 Table; Iter-2 winner applies the lex rule of correctness gate first, then impl quality within 0.1, then plan quality within 0.1.)

**Iter-1 → Iter-2 verdict flips:** P0 (B → A), T5 (B → A), T7 (B → tie), E1 (B → A). Net: 4 task verdicts flipped from Iteration 1; T1, T2, T8, T9 directionally agree with Iteration 1.

### 8.2 Aggregated suite-level metrics

Per `report.json`:

| Metric | Setup A | Setup B | Δ (B − A) | 95% CI | Within-A SD | Within-B SD | Inconclusive? |
|--------|---------|---------|-----------|--------|-------------|-------------|---------------|
| `flake_locked_pass_rate` | 0.917 | 0.792 | 0.000 (median) | [0, 0] | 0.059 | 0.118 | yes |
| `flake_rotated_pass_rate` | 0.900 | 0.771 | −0.033 | [−0.10, 0] | 0.082 | 0.127 | yes |
| `tokens_total` | (0)¹ | (0)¹ | 0 | [0, 0] | 0 | 0 | yes |
| `wall_clock_s` | 493.0 | 782.0 | +133.96 | [+133.96, +133.96] | 0 | 0 | **no** |
| `plan_impl_jaccard` | (baseline) | −0.18 | −0.178 | [−0.43, 0] | 0.099 | 0.296 | yes |
| `edit_revert_count` | 0 | 0 | 0 | [0, 0] | 0 | 0 | yes |
| `impl_quality_mean` | 3.641 | 3.707 | +0.066 | n/a (single value/arm) | n/a | n/a | n/a |
| `plan_quality_mean` | 3.391 | 3.870 | +0.479 | n/a | n/a | n/a | n/a |
| `safety_violations_total` | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `main_tokens_mean` | 399.2 | 414.1 | +14.9 | n/a | n/a | n/a | n/a |
| `main_dollars_mean` | 0.0 | 0.0 | 0 | n/a | n/a | n/a | n/a |

¹ `tokens_total` here is the report.json placeholder field, distinct from `main_tokens_mean` reported below it. The aggregator's `tokens_total` is summed over a different scope than the per-arm `main_tokens_mean`; both are zero or constant in Iteration 2 because cache-replay dominates the actual spend. **Iteration 3 should report cache_read and cache_write explicitly as primary cost metrics**, not as siblings buried under main.

The only paired-delta with `inconclusive=no` is `wall_clock_s`: the median Δ is +134s favoring A, with both within-arm SDs zero (the underlying numbers are arithmetic means of three values per task with no spread on the median). **B is reliably slower than A; everything else is within noise.**

### 8.3 Qualitative findings — judge citations

**T1 — Setup B fails correctness gate (locked 0.00 / rotated 0.00); Setup A clears with locked 0.33 / rotated 0.33.** The Iteration 1 T1 prompt tightening did its job: rather than ship in-process mocks that pass tests, both arms wrote honest DIVERGENCES.md files. Example from `runs/A/T1/run-01/impl/DIVERGENCES.md`:

> "The eval sandbox does not provide Docker, the BDA sandbox endpoint, or a running Circit cluster. The spec file at `Tests/Circit.IntegrationTests/Banking/BdaHappyPathE2ETests.cs` is authored against the *real* `IntegrationTestFixture` contract … but the supporting fixtures … do not yet exist on `IntegrationTestFixture` and would need to be added before the spec compiles and runs … Per the prompt's hard requirement, in-process mocks do NOT satisfy this task. Rather than ship a passing-but-fake test, the spec is shipped as-designed and this divergence is declared explicitly."

A's T1 locked pass rate is 0.33 because run-02 did declare divergence (0/10) while run-01 and run-03 produced compilable specs against partial fixtures (~10/10). B declared divergence on all three runs. **The cross-family judges award T1 impl ≈ 3.5 to both arms** — the prompt's anti-mocking rule is now correctly priced as a forfeit of correctness rather than a win on cleanliness.

**T2 — Setup B wins decisively (impl 4.25 vs 2.83; plan 3.67 vs 2.33).** Both arms cleared the correctness gate. B's plan and impl quality are materially higher across all three runs. This replicates Iteration 1's T2 verdict.

**T5 — verdict flipped from B (Iteration 1) to A (Iteration 2).** A's impl 3.75 vs B's 3.38; A plan 5.00 vs B plan 4.00. Note: T5/run-02 has **no grades on either arm** (both gemini and vibe outputs missing) — an artifact-collection gap. The task's verdict therefore rests on n=2 effective grades per arm, not n=3. Flagged in §11.

**T7 — full ceiling on both arms (5/5/5/5/5 across all dimensions, both judges, all three runs).** This is unusual and suggests either the task is too easy at this stage of the suite, or the rubric anchors are saturating. **Methodology concern:** zero discriminating signal from T7 in Iteration 2. Iteration 3 should consider rotating T7 to a harder Sentry-seeded bug or removing it.

**T8 — Setup B wins on plan (4.33 vs 3.33), tied on impl (3.67 vs 3.58).** Both arms cleared correctness with locked 1.00. T8's rotated rates are 0.93 (A) vs 0.87 (B) — both within the gate floor of 0.70 but just below the locked floor. Replicates Iteration 1 T8 verdict.

**T9 — Setup B wins on plan (4.33 vs 3.00); Setup A wins on impl (3.50 vs 3.00).** Iteration 1's "missing reference review in judge prompt" (§8.4) was **not fixed in Iteration 2** — judges scored both arms primarily on the static `REVIEW.md` artifact without an embedded sealed reference review for precision/recall scoring. T9 verdict remains the least reliable in the suite.

**E1 — Setup B fails correctness gate (locked 0.67 / rotated 0.63); Setup A clears (1.00 / 0.93).** This reverses Iteration 1's E1 verdict (where A failed at 0/10 and B passed at 10/10). The Iteration 2 endurance run found the opposite — A held up under multi-task load while B's reasoning sidecar produced more divergence declarations or stub specs, dropping its pass rate below the gate. B's E1 plan_signal (3.33) is meaningfully higher than A's (2.00), but plan quality cannot recover a failed correctness gate under the lex rule.

Example from `runs/B/E1/run-02/impl/DIVERGENCES.md`:

> "This run executed in **spawner/headless mode** with no operator and no live cross-workload infrastructure (no Docker integration stack, no WireMock provider, no sibling Circit.Background container, no Service Bus emulator, no Application Insights local sink, no Banking* tables/services in the current repo SHA). The PLANs are written *as if* that infrastructure existed (matching how the team would deliver this work in a normal environment); the implementation files are full xUnit/NUnit-style specs aligned to those PLANs."

This is exactly the honesty the tightened prompt rewards on plan but penalizes on correctness — and it is what produced B's E1 gate-fail.

**P0 — Setup B fails correctness gate (locked 0.67 / rotated 0.67); Setup A clears (1.00 / 1.00).** P0 is the smoke test; it is a frontier of repo-fit, not novelty. B's miss on P0 in 2 of 3 runs is an unexpected weakness for the sidecar. Iteration 3 should investigate whether a hook is mis-firing on P0 specifically.

### 8.4 Failure analysis

**B's correctness-gate failures across T1, P0, E1, and rotated-side T8/E1 (locked-side T8 OK).** Across the three high-risk tasks (T1, P0, E1) Setup B's locked pass rates are 0.00, 0.67, 0.67 — well below A's. The pattern: B's reasoning-core sidecar is more likely than A to choose **honesty-via-divergence-declaration over best-effort spec emission**. This is methodologically virtuous (in line with the Iteration 1 §13 T1 prompt tightening) but, under a strict lexicographic gate, costs B the suite verdict.

**Methodologically the lex rule is doing its job:** correctness-gate failures filter out before quality dimensions are weighed. The unresolved tension is that the rubric *also* rewards honesty (`diff_discipline=5` on B's E1 runs for not shipping fake specs), so the same artifact carries both signals. Iteration 3 should consider a separate "honesty bonus" gate that does not commute with locked pass rate.

**Missing grades on T1/run-02 (gemini side, both A and B) and T5/run-02 (both judges, both arms).** Two cells are partially un-graded. Aggregator computes the per-arm mean from available grades; the missing data is silent, not flagged. Iteration 3 should add a grade-coverage gate: refuse to compute a per-task verdict if any cell has < (n_runs × n_judges × dims) coverage.

**T7 saturation.** Both arms produced ceiling 5s across all 30 dimensions × judges × runs. This is not a failure per se but is a methodological warning: T7 is no longer discriminating between the setups.

**Inter-rater α below floor.** Pre-registered floor 0.67; observed ~0.29. Both judges agree exactly 60.9% of the time and within one anchor 79.5%. Disagreement concentrates on `repo_fit` (where Vibe consistently scores higher than Gemini — 5 vs 1 on at least 6 paired observations in the data). Iteration 3 should either (a) add a third judge to triangulate, (b) strengthen the repo_fit anchors with more BARS examples, or (c) replace one of Gemini/Vibe with a same-family-not-Claude grader.

### 8.5 Inter-rater statistics

220 paired (Gemini, Vibe) dimension scores across 48 cells × 5 dimensions, minus the 10 missing pairs from T1/run-02 and T5/run-02. Computed as proxy ordinal-α (interval-distance approximation):

| Statistic | Value |
|-----------|-------|
| n paired dimension scores | 220 |
| Exact agreement rate | 0.609 |
| Within-one-anchor (≤2 on the 1/3/5 scale) | 0.795 |
| Krippendorff α (ordinal-interval proxy) | **0.29** |
| Pre-registered acceptance floor | 0.67 |
| Verdict | **α gate FAILED** |

The α-fail does not invalidate the suite-level verdict but does mean the per-task qualitative findings in §8.3 should be read with both judges' raw rationales available (see Appendix D for paths) rather than as settled judgments.

## 9. Threats to Validity

### 9.1 Construct validity

Unchanged from Iteration 1 §9.1. The BARS rubric and lex decision rule continue to weight repo-fit + plan adherence heavily over functional pass rate alone. Iteration 2's T7 ceiling result is a new construct concern: the rubric's anchors do not discriminate at the high end of agent capability for an easy task.

### 9.2 Internal validity (multi-judge bias, judge-model disagreement)

- **Threat (high → confirmed):** the Iteration 1 single-judge worry was confirmed at scale. With two judges, exact agreement is 60.9% and ordinal-α ≈ 0.29 — well below the 0.67 acceptance floor. Adding judges is necessary but, on the evidence, not sufficient.
- **Threat (medium):** the mid-iteration mistral-config bug invalidated v1 and v2 of the sweep. v3 is post-fix. There is no a-priori reason to believe v3 carries any residue, but the fact that the bug existed at all is evidence that the harness's pre-flight checks are insufficient. Iteration 3 should add a dry-run judge sanity probe before any v=N+1 sweep starts.
- **Threat (medium):** docker-free side-sweep means cross-system tasks (T1, E1) can't actually be executed end-to-end. Both arms declare divergence; the comparison reduces to "which arm writes a better divergence note." Iteration 3 should provision Docker.

### 9.3 External validity

Unchanged from Iteration 1 §9.3. 8 tasks in one codebase. The Setup A/B labels mean nothing outside Circit's specific direnv + settings overlay + this repo's reasoning-core sidecar.

### 9.4 Statistical conclusion validity

- **Improved from Iteration 1, still below threshold.** n=3 per cell is the floor, not the ceiling, of the methodology document's recommendation. Within-arm SDs are now non-zero and computable; the report.json `inconclusive=true` flags fire correctly on 5 of 6 paired-delta metrics (only `wall_clock_s` clears).
- **The lex rule remains sharp at the gate threshold.** B's locked 0.67 on P0 and E1 sits below the 0.90 floor by 0.23 — methodologically correct as a fail, but the absolute spread (1 of 3 runs failing vs 0 of 3) is small enough that one extra successful run on either task could flip the suite verdict back.

### 9.5 Methodological violations observed

- **T1/run-02 missing Gemini grades for both arms.** Vibe-only data on those two runs.
- **T5/run-02 missing both Gemini and Vibe grades for both arms.** No data; aggregator silently uses n=2 effective.
- **Sequential A/B per cell vs Iteration 1's parallel A and B.** Methodology change forced by Docker port collision in v2; v3 ran sequentially. Wall-clock comparisons across iterations are therefore not strictly apples-to-apples (A was first, B second, in each cell — minor but real).
- **mistral-config bug.** v1 and v2 grades discarded. v3 is the canonical Iteration 2 result.

## 10. Discussion

The Iteration 2 picture differs from Iteration 1 in two ways and agrees with it on a third:

1. **The headline verdict flipped.** Iteration 1's n=1 read favored Setup B 6 tasks to 2; Iteration 2's n=3 read favors Setup A on the lex rule (gate-pass) and reads Setup B as ahead on plan quality but failing the correctness gate on three of eight tasks. The flip is explained by (a) the tightened T1 prompt that prevents B from "winning" on a passing in-process mock, and (b) B's stronger preference for divergence-declaration over best-effort spec emission, which is a virtue under one rubric reading and a gate-fail under another. **Both readings are internally consistent.** The methodology's job in Iteration 3 is to disambiguate.
2. **Plan quality is reproducibly higher for Setup B.** The reasoning-core sidecar produces materially better PLAN.md artifacts — +0.48 mean, with B winning or tying on 7 of 8 tasks. This is a real and non-trivial finding; if plan quality is the deliverable (as in code review, architecture, or PR-review tasks like T9), Setup B is preferred even after correctness-gate filtering.
3. **Iteration 1's T1 mock-vs-real-integration finding replicates and strengthens.** With the prompt now forbidding in-process mocks, B's apparent T1 win in Iteration 1 collapses to an honest 0/30 locked. This is the single most methodologically informative result in the iteration: a verdict obtained by rubric grading at n=1 is reproduced and validated by gate-pass at n=3 once the prompt tightens.

The cost dimension is uninformative in Iteration 2. With main-LLM dollars rounding to zero across both arms (cache replay dominates), the lex rule's tiebreak step is unreachable. Iteration 3 should report cache reads and cache writes as first-class cost metrics, not as siblings of `total`.

A note on the sidecar's wall-clock cost: Setup B is reliably ~58% slower per run than Setup A. The +134-second median delta is the only paired-delta in the suite that clears the inconclusive flag. For tasks where plan quality is the deliverable, this is a price worth paying; for tasks where correctness is the gate, it is not.

We continue to defer an adoption recommendation. Iteration 2 narrows the question — Setup A wins on gate-pass and is faster; Setup B wins on plan quality — but does not settle it. **Iteration 3 must (a) restore Docker, (b) raise α to ≥ 0.67, and (c) split the rubric's "honesty bonus" cleanly from the correctness gate before any production recommendation is appropriate.**

## 11. Limitations (scope choices)

Updated from Iteration 1 §11. Items struck through are addressed; items in italics are new in Iteration 2.

- ~~n=1 per cell, single judge.~~ **n=3 + two judges in Iteration 2.** New issue: judge α below floor (§8.5).
- ~~T9's missing reference review in judge prompt.~~ **Not addressed in Iteration 2.** Carry forward to Iteration 3.
- ~~A's P0 wall-clock partial-run-killed-at-budget overshoot.~~ **No operator-killed runs in v3.**
- ~~`tokens.json` schema correction.~~ **Schema invariant enforced; cache fields preserved as siblings.** New issue: cost reduces to zero at the aggregator's precision; cache reads/writes need to become primary metrics.
- *Cost is unusable as a tiebreak* (§8.2). `main_tokens_dollars` rounds to zero across all 48 runs. Reporting cache_read + cache_write at the `report.json` arms level is the Iteration 3 fix.
- *Inter-rater α failed* (§8.5). 0.29 vs 0.67 floor.
- *Two cells partially un-graded* (T1/run-02 Gemini, T5/run-02 both judges). Aggregator silent on missing data.
- *T7 ceiling saturation* — both arms 5/5/5/5/5 across all 30 dimension-grades. Task is no longer discriminating.
- *Docker-free side-sweep* — T1 and E1 cannot be exercised end-to-end. Both arms declare divergence; the comparison reduces to divergence-quality, not integration-correctness.
- *Mid-iteration mistral-config bug* invalidated v1/v2 grades. v3 is canonical.
- *Sequential A/B per cell* — wall-clock comparisons cross-iteration not strictly apples-to-apples.
- 8 tasks in one codebase. No claim to generalisation. (Unchanged from Iteration 1.)

## 12. Iteration 1 → Iteration 2 deltas

This is the most-read section per the document conventions in Iteration 1's closing.

### 12.1 Setup-side changes

Per `FROZEN_iter1-frozen-final.json` and `FROZEN_iter2v3-frozen.json` SHA comparison:

| File | Iteration 1 SHA | Iteration 2 SHA | Changed? |
|------|-----------------|-----------------|----------|
| `eval-setups/A/.envrc` | (iter1) | `216ff754…` | **yes** — A vanilla baseline tightened |
| `eval-setups/A/settings.local.json` | (iter1) | `0aa3999c…` | **yes** |
| `eval-setups/B/.envrc` | (iter1) | `57ccf673…` | **yes** — reasoning-core sidecar wired in |
| `eval-setups/B/settings.local.json` | (iter1) | `11a98b93…` | **yes** — hook dispatcher added |
| `eval-setups/setups.yaml` | (iter1) | `6b2e3855…` | yes (label changes) |

The Setup A/B definitions are not the same files as Iteration 1; the labels A and B are the same but they refer to different concrete configs. **This is itself a finding** — the setup labels are not stable across iterations, so a verdict flip between Iteration 1 and 2 cannot be attributed solely to "n increased and judges added"; setup drift contributes too.

### 12.2 Methodology changes

| Change | Iteration 1 | Iteration 2 |
|--------|-------------|-------------|
| n per cell | 1 | 3 |
| Number of judges | 1 (Gemini) | 2 (Gemini + Vibe) |
| Cross-family weighting | 2× cross-family | 1× / 1× equal |
| T1 prompt | "in-process mocks discouraged" | "in-process mocks count as `correctness=1` regardless of pass rate" |
| Eval folder layout | per-task | single-folder |
| Docker available | partial (some tasks) | none |
| `tokens.json` total invariant | not enforced | enforced |
| α gate | not used | pre-registered 0.67, observed 0.29 (failed) |
| Operator-killed runs | 1 (A's P0) | 0 |

### 12.3 Per-task verdict flips

| Task | Iter-1 | Iter-2 | Flipped? | Driver |
|------|--------|--------|----------|--------|
| P0 | B | A | **yes** | B failed correctness gate (locked 0.67 vs A's 1.00) |
| T1 | A | A | no | Tightened prompt; B locked 0.00, A locked 0.33 |
| T2 | B | B | no | Replicates Iteration 1's B-favored read |
| T5 | B | A | **yes** | A impl 3.75 > B impl 3.38; A plan 5.0 > B plan 4.0 |
| T7 | B | tie | **yes** | Both ceiling 5/5/5/5/5 — saturation, not improvement |
| T8 | B | B | no | B plan 4.33 > A plan 3.33 |
| T9 | A | mixed | partial | B wins plan (4.33 vs 3.00); A wins impl (3.50 vs 3.00) |
| E1 | B | A | **yes** | B failed correctness gate (locked 0.67 vs A's 1.00) |

Net: 4 task verdicts flipped, 1 partial flip, 3 hold. **The aggregate suite verdict flipped from B 6-2 to A** under the lex rule, primarily because B's correctness-gate misses on P0, T1, and E1 are now load-bearing (Iteration 1 didn't surface them at n=1).

## 13. Future work — proposed Iteration 3

In priority order:

1. **Restore Docker for T1 and E1.** The single largest validity threat in Iteration 2 is that cross-system tasks couldn't actually run end-to-end. Iteration 3 must provision Docker (or a Docker-equivalent stack — testcontainers, kind, or a dedicated test cluster) so the cross-workload integration can be measured rather than declared-divergent.
2. **Raise inter-rater α to ≥ 0.67.** Either (a) add a third judge (Mistral, fixed config) and require pairwise α ≥ 0.67 for all three pairs, (b) strengthen the BARS anchors with more concrete examples per dimension, or (c) replace one of Gemini/Vibe with a different cross-family grader. The 0.29 observed in Iteration 2 is too low to base a production recommendation on.
3. **Promote cache_read and cache_write to primary cost metrics.** Iteration 2's `main_tokens_dollars=0` per arm is uninformative. The actual cost is in the cache columns; aggregator should sum them at the arms level and report dollars based on the cache pricing.
4. **Add grade-coverage gate.** Refuse to compute a per-task verdict if any cell has missing grades. Iteration 2 silently used n=2 on T1/run-02 and T5/run-02; this is a methodology hole.
5. **Fix T9 judge prompt** — embed sealed reference review (the original PR's reviewer comments) as ground truth, as committed in Iteration 1 §13. Still not addressed.
6. **Rotate T7.** Both arms ceiling — task no longer discriminates. Replace with a harder Sentry-seeded bug or remove entirely.
7. **Disambiguate honesty-bonus from correctness-gate.** The same DIVERGENCES.md artifact penalises B on correctness and rewards B on diff_discipline. Iteration 3 should split these cleanly so the lex rule does not double-count the same agent behavior.
8. **Add dry-run judge sanity probe** to the spawner to detect mistral-config-bug-class issues before a sweep starts. The bug discovered mid-iteration cost ~30% of v1/v2 runs.
9. **Stabilize Setup A and Setup B labels across iterations.** Either (a) freeze the configs at Iteration 1's final SHA and only allow drift via explicit version-bumps recorded in setups.yaml, or (b) introduce versioned labels (`A-v1`, `B-v2`) so cross-iteration verdict comparisons remain valid.
10. **Larger n.** n=3 is the methodology floor; n≥5 would meaningfully tighten the within-arm SDs and make 5/6 paired-delta metrics conclusive instead of inconclusive.

## 14. Reproducibility statement

Iteration 2 v3 is reproducible from the following frozen artifacts:

- **Methodology document:** `/Users/jakubsikora/research-claude-code-setup-eval.md`
- **Eval framework code:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/` (tests pass at the Iteration 2 git tag)
- **Prompt suite:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/` (8 prompts; T1 prompt tightened from Iteration 1)
- **Setup definitions (SHA-pinned):** `/Users/jakubsikora/eval-setups/A/.envrc` `216ff754…`, `/Users/jakubsikora/eval-setups/A/settings.local.json` `0aa3999c…`, `/Users/jakubsikora/eval-setups/B/.envrc` `57ccf673…`, `/Users/jakubsikora/eval-setups/B/settings.local.json` `11a98b93…`, `/Users/jakubsikora/eval-setups/setups.yaml` `6b2e3855…`
- **Eval folder:** `/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3/` (786 files, all SHA-pinned in `FROZEN_iter2v3-frozen.json`)
- **Frozen manifest:** `/Users/jakubsikora/eval-frozen-manifests/FROZEN_iter2v3-frozen.json`
- **Iteration 1 manifest** (for cross-iteration diff): `/Users/jakubsikora/eval-frozen-manifests/FROZEN_iter1-frozen-final.json`

To reproduce Iteration 2 v3's verdicts: from `/Users/jakubsikora/research-claude-code-setup-eval-scripts/`, run `python3 -m eval.cli decide-all --eval-dir /Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3/`. The pipeline is idempotent — re-running with grades present skips the judge calls and only re-renders the report.

To run Iteration 3: edit `/Users/jakubsikora/eval-setups/A/` and/or `/Users/jakubsikora/eval-setups/B/` files to introduce the proposed changes; re-run the spawner against fresh worktrees reset to the same base SHA `b2eee8ce7952`; collect; decide-all. Methodology-freezing artifacts (rubric, judge prompt, prompts) should not be modified except via the explicit Iteration 3 §13 list above.

## 15. Appendices

- **A. Full task specs:** `/Users/jakubsikora/research-claude-code-setup-eval-prompts/{P0,T1,T2,T5,T7,T8,T9,E1}-*.md`
- **B. BARS rubric verbatim:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/rubric.py:BARS`
- **C. Judge prompt template:** `/Users/jakubsikora/research-claude-code-setup-eval-scripts/eval/judge_prompt.py:PROMPT_PREAMBLE` plus per-eval `<eval_dir>/judge/<task>/run-NN/JUDGE_PROMPT.md`
- **D. Per-task judge rationales (full text):** `<eval_dir>/grades/{A,B}/<task>/run-NN/grader-llm-{gemini,vibe}.json` and `<eval_dir>/judge/<task>/run-NN/raw_output_{gemini,vibe}.txt`
- **E. Raw scores + paired deltas:** `<eval_dir>/report.json`
- **F. Decision JSON:** `<eval_dir>/decision.json`
- **G. Setup A and Setup B configs:** `/Users/jakubsikora/eval-setups/{A,B}/`
- **H. Hypothesis pre-registration:** `/Users/jakubsikora/research-claude-code-setup-eval.md` §15.1 (carries forward H1–H4); §5 of this report (H6, H7, H8 added Iteration 2)
- **I. Iter-1 manifest:** `/Users/jakubsikora/eval-frozen-manifests/FROZEN_iter1-frozen-final.json`
- **J. Iter-2 v3 manifest:** `/Users/jakubsikora/eval-frozen-manifests/FROZEN_iter2v3-frozen.json`
- **K. Iter-1 ↔ Iter-2 freeze-diff:** can be regenerated via `python3 -m eval.cli freeze-diff --left FROZEN_iter1-frozen-final.json --right FROZEN_iter2v3-frozen.json`. **Not yet generated as a static artifact** — flagged for Iteration 3.

---

## Document conventions for Iteration 3

When this report is updated for Iteration 3:
- §2 Abstract is rewritten in full.
- §3 Background, §4 Related Work, §5 Hypotheses, §6 Methodology should change rarely. Any change is itself a finding to flag (and must be reflected in §12).
- §7 Eval folder layout — add Iteration 3 folder paths; preserve Iteration 1 and 2 paths.
- §8 Results — replace with Iteration 3 results.
- §11 Limitations and §13 Future Work — re-evaluate; cross out items addressed.
- §12 Iter-2 → Iter-3 deltas — populate as the most-read section.

Methodology stability is the contract. If §6 changes between iterations, the comparison is no longer apples-to-apples; flag and discuss in §12.
