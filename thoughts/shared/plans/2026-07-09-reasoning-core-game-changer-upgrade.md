---
date: 2026-07-09
status: draft — adversarially reviewed, five revisions applied
author: reasoning-core research swarm + adversarial synthesis
supersedes: thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md
adversarial_review:
  - 2026-07-09 — review 1, verdict "RETHINK"; findings addressed in revision 1
  - 2026-07-09 — review 2, verdict "RETHINK"; findings addressed in revision 2
  - 2026-07-09 — review 3, verdict "FIX-FIRST"; findings addressed in revision 3
  - 2026-07-09 — review 4, verdict "FIX-FIRST"; findings addressed in revision 4
  - 2026-07-09 — review 5, verdict "FIX-FIRST"; findings addressed in revision 5
---

# reasoning-core Game-Changer Upgrade Plan

> **Note:** This draft was reviewed five times by the adversarial reviewer (`claude-fable-5`, advisor mode). Reviews 1 and 2 returned "RETHINK"; reviews 3–5 returned "FIX-FIRST." Review 5 noted that no re-architecting is required and that pillars, phasing, and kill-criteria discipline are sound after four revisions. Revision 5 fixes: replayable-input check for offline random-mamba control, realistic oracle tier budgets with advisory-only timeouts, frozen contract applied symmetrically to both CTS arms, power-calculation-based eval decision rule, human-verified PRM subset + LoRA-scale fine-tune, PID-inclusive session key with explicit cross-restart limitation, defined `repo_idiom_adherence_delta_norm` with floor, and private `~/.cache/reasoning-core/rc-scratch/` location.

## Executive summary

reasoning-core is at a credibility inflection point. The 2026-06-01 patch made the product more honest, but the production audit (68,794 events, 2026-06-01 → 2026-07-08) shows the neural gate is effectively offline: 87.7% of SSM-tagged events fail-open, the marketed 11-dim risk vector appears in 8 of 68,794 events, and only 4.1% of hard blocks are reasoning-quality blocks. The system today is a useful local symbolic tripwire that protects its own files and catches shell-escape bypasses — not the structural-reasoning copilot the docs describe.

This plan retires three shaky foundation assumptions, fixes the honesty crisis, and rebuilds the product around the one thing that actually moved the needle in audits: **plan↔implementation alignment**. The game-changing version of reasoning-core is not a faster SSM embedder or a bigger risk vector. It is a **local, execution-grounded contract enforcer** that reads the agent's plan, compiles it into an enforceable policy, verifies every edit against that policy and fast oracles, and closes the loop with calibrated recovery hints — all before the edit lands on disk.

**Definition of "game changer":** A measurable, statistically significant improvement in `agent_reasoning_efficiency` (net reasoning-quality true-positive blocks per session-second) and in Clean Task Success on SWE-bench Verified, with ≥50% of any lift coming from reasoning-quality signals (contract/oracle/PRM blocks) rather than self-protection blocks. Exact targets will be set after a 20-task pilot in Phase 1; initial working hypothesis is a 2–3× efficiency improvement and a 5–10 pp CTS lift, with a stretch goal of +15 pp if the pilot supports it.

---

## 1. Current reality (what the audit says)

### 1.1 The product is a symbolic/self-protection gate with neural marketing

| Fact | Number |
|---|---|
| Events parsed (Jun 1 – Jul 8) | 68,794 |
| Hard blocks | 904 (1.31%) |
| Blocks from shell-escape / guard-file / lang-lock | 602 (66.6%) |
| Reasoning-quality blocks (regression, plan_impl_drift, decomposition) | 37 (4.1%) |
| SSM-tagged events that fail-open / fail-closed | 8,454 / 9,641 (87.7%) |
| Events containing the 11-dim risk vector | 8 |
| Events containing `architectural_impact_score` | 0 |
| `signal_source="prm"` events | 0 |

### 1.2 The most important signals are dead or disconnected

- **`coherence_delta`** is degenerate: 98% of sampled values sit in `[0.00, 0.10)`. Recalibrating the threshold to the empirical p95 (0.09) does not restore signal; it just fires on 5% of edits by construction.
- **Phase-2 risk dims** (`session_centroid_drift`, `project_fan_in`, `project_coupling`) never emit because the hook never POSTs a `/baseline` to establish a session corpus.
- **`gate_prm`** is implemented (`src/hooks/_dispatch.py:681-744`) but `pre_edit_guard.py` never calls it, so zero PRM events exist in production.
- The **symbolic rule engine** is documented as co-emitted with the neural vector but is never invoked by the PreToolUse path.

### 1.3 Installed defaults contradict marketed defaults

`install.sh:121-172` ships `RC_SHADOW_MODE=1` (log-only) and leaves `RC_PLAN_GROUNDING` / `RC_BEST_EFFORT_SPEC` unset (→ disabled). `README.md:105-119` claims enforce-by-default and plan-grounding-on. The benchmark wins came from Setup-B where those levers were explicitly enabled.

### 1.4 Latency killed the neural gate

The SSM sidecar p50 was ~3 s and p95 was 58 s before the hard cap. The June-1 hard cap (`S2_HARD_CAP_MS=1500/3000`) eliminated the 60-second tail but also caused mass fail-open: the sidecar cannot complete scoring within the cap. The promised "symbolic fallback on timeout" is only a stderr message; the actual behavior is fail-open or fail-closed.

---

## 2. Foundation assumptions to retire or evolve

### Retire: "SSM embedding + chord distance detects regressions"

The evidence says no. `mamba-130m` is a general Pile-LM checkpoint, not code-pretrained; it is fed AST-token streams that discard scope/type/call-graph structure; and the chord-distance metric is degenerate in production. The SSM path should be demoted from primary signal to one input among many.

### Retire: "An 11-dimensional risk vector is the right abstraction"

Production emits 8 dims. Three of those dims are near-collinear depth proxies (`fan_in↔fan_out r=+0.708`, `fan_in↔depth r=+0.876`). The normalizers are arbitrary constants. The vector is not actionable for users and not predictive enough to gate on.

### Evolve: "Plan-grounding prevents scope creep"

The concept is sound and produced the largest concentration of reasoning-quality signal. But the current implementation only checks whether the edited file appears in `PLAN.md`, not whether the edit content matches the plan claim. The mechanism must evolve from file-list matching to a **plan-to-contract compiler**.

### Keep: "Local, hook-based, repo-scoped enforcement"

This is reasoning-core's only durable strategic moat. No major vendor offers a portable, editor-agnostic, evidence-based local enforcement layer. The upgrade must preserve local-only operation, loopback binding, and zero telemetry.

---

## 3. Strategic pillars

The upgrade is organized into four pillars. Each pillar directly addresses a user pain point from the community research and an empirical gap from the audit.

| Pillar | User pain | Audit gap | Outcome |
|---|---|---|---|
| **P1 — Honest defaults & dead-code burial** | Rules ignored / not enforced | Installed defaults ≠ marketed defaults; dead gates | Users get the product described in the README |
| **P2 — Plan-as-contract compiler** | Agent edits files I didn't ask for | Plan-grounding warn-only and file-list-only | Machine-enforceable plan scope |
| **P3 — Execution-grounded verify-and-repair loop** | Token waste from off-plan loops; hallucinated APIs | No scratch-clone/test oracle in the hook | Catches regressions before disk |
| **P4 — Self-improving calibration from git history** | Drift in conventions; threshold rot | Thresholds arbitrary; calibration off by default | Guardrail improves the longer it runs |

---

## 4. 90-day implementation plan

### Phase 0 — Honesty patch (week 1)

Before any "game changer" work, fix the honesty crisis. A product that markets neural reasoning but ships a symbolic tripwire cannot credibly ask users to trust a bigger upgrade.

**Deliverables:**
1. **Fix doc drift.** Update `HOW_IT_WORKS.md`, `ARCHITECTURE.md`, `CONFIGURATION.md`, and `README.md` to match the actual code defaults, the 8-dim vector, chord-distance scale, and timeout behavior. Do **not** change enforcement defaults yet.
2. **Bury or wire dead gates.** Either call `gate_rule_engine` and `gate_prm` from `pre_edit_guard.py`, or delete them and remove the marketing claims. Same for `cumulative_drift`, Phase-2 risk dims, and the 11-dim vector.
3. **Implement real symbolic fallback.** On `S2_HARD_CAP_MS` timeout, invoke `gate_rule_engine` + `gate_lang_lock` + `gate_plan_grounding` and emit `signal_source="symbolic_fallback"`.
4. **Validate with `random-mamba` control offline.** First, confirm the audit events contain replayable inputs (file contents / diff hunks / AST-token streams). If they do, replay both `RC_EMBEDDER=random-mamba` and `mamba-130m` against the existing corpus plus regression fixtures, scoring the same historical inputs under both embedders. Hand-label ~200 of the resulting block/would-be-block events as correct/incorrect to compute precision. If audit inputs are not replayable, run the comparison on fixtures only and use a 7-day live shadow as the **primary** evidence for the keep/remove decision, not just confirmation. Scope the comparison to **SSM regression detection on AST-token streams**. If real weights are not significantly better, remove the SSM from the regression-detection path. Make a binary decision: either define a concrete PRM/project-index feature that requires the SSM and validate it, or remove the SSM from the hot path entirely.
5. **Add explicit override/confirmation CLI signals.** Extend `rc bypass-next` to emit an `operator_override` audit event, and add `rc confirm-next` to emit an `operator_confirmed` event. These become the ground-truth signal for false-positive measurement.
6. **Resolve the default-posture contradiction explicitly. Phase 0 ships Option B (honest opt-in).**
   - **Option B (Phase 0):** Keep `RC_MODE=advise` (log-only / warn) in `install.sh`, remove all "enforce-by-default" claims from README/BENCHMARKS, and document that users must run `rc enable-enforcement` after reviewing a 48-hour shadow report. This matches what the code actually does today.
   - **Option A (Phase 2 exit deliverable):** After the contract compiler and execution oracles pass their kill criteria, flip the install default to `RC_MODE=copilot` with a first-run wizard that scaffolds `PLAN.md` and explains the three modes. `autopilot` remains opt-in.
   The plan assumes **Option A** is the eventual target, but it is gated on Phase 2 success.

**Kill criteria:** If honesty-patch PR cannot make README, install script, and code defaults consistent within one week, pause game-changer work until it can.

### Phase 1 — Plan-to-contract compiler (weeks 2–4)

Replace file-list plan-grounding with a machine-readable contract derived from `PLAN.md`.

**Deliverables:**
1. **Contract schema.** Define a JSON/YAML contract with sections:
   - `allowed_paths`: glob list of files the agent may touch.
   - `forbidden_paths`: files/modules the agent must not touch.
   - `required_tests`: tests that must pass or be added.
   - `import_rules`: forbidden imports per module (replaces/adopts `.reasoning-core/rules.yaml`).
   - `invariants`: free-form assertions the agent must preserve (e.g., "auth layer never imports payments layer").
   - `phases`: ordered list of implementation phases; each Edit/Write is checked against the current phase.
2. **Parser.** A robust but lightweight parser (`src/plan_contract.py`) that reads `PLAN.md` and extracts the contract. Start with structured markdown sections; fall back to LLM extraction only when necessary and log confidence.
3. **Enforcement engine.** For every Edit/Write:
   - Path check: is the file in `allowed_paths` and not in `forbidden_paths`?
   - Phase check: does the edit match the current phase?
   - Import check: does the diff introduce forbidden imports?
   - Test check: if a phase claims a test will be added/updated, verify the test file is touched.
4. **Claim↔hunk alignment.** For blocking decisions, use **path overlap only**: a hunk aligns with a claim if the edited file is in the claim's `allowed_paths` and the claim phase is active. Separately, run a lightweight embedding/retrieval alignment in the background as an advisory signal. It must not add latency to the blocking path. Report alignment confidence in the audit log.
5. **Phase-intent catch-all.** Define a default rule: edits to files within the current phase's scope that do not violate any explicit clause are allowed, even if the exact helper/refactoring is not named in a claim. This prevents blocking common implementation details.
6. **Block/warn semantics.** `RC_PLAN_GROUNDING=1` warns; `RC_PLAN_GROUNDING=2` blocks. Default remains `=1` until Phase 2 oracles pass kill criteria. Every block surfaces the violated contract clause.

**Expected effect:** 50%+ reduction in "agent touched files I didn't ask for" in internal scope-creep fixtures.

**Kill criteria:** If contract extraction F1 < 0.80 on 50 hand-annotated `PLAN.md` files, keep it warn-only.

### Phase 2 — Execution-grounded Synthesize-Check-Refine loop (weeks 4–7)

Add fast oracles that apply the diff in a scratch clone and check it before the real filesystem is touched.

**Deliverables:**
1. **Cumulative patch tracker.** Maintain the session's pending diff state. Use a stable session key: prefer Claude Code's session ID when present; otherwise fall back to `<repo_root_hash>-<branch>-<ppid>-<pid>`. Use a repo-scoped advisory lock to prevent concurrent agent processes from corrupting shared state. Persist in `$RC_CACHE_DIR/rc-scratch/<session_key>/pending.patch` (default `~/.cache/reasoning-core/rc-scratch/`, mode `0700`). Cross-restart persistence is only guaranteed when a stable session ID is available; the fallback key intentionally changes on restart to avoid chimera diffs from concurrent or recycled PIDs. Oracles run against the cumulative patch, not isolated hunks, so legitimate intermediate edits do not false-positive. Reset on agent commit, task boundary, or `rc reset`. Add a TTL sweep for orphaned scratch dirs.
2. **Tiered scratch-clone oracles.** Create **one persistent worktree per session** (`git worktree add` or `clone --shared` once) at `$RC_CACHE_DIR/rc-scratch/<session_key>/worktree/` (default `~/.cache/reasoning-core/rc-scratch/`, mode `0700`). Per edit, reset the worktree and apply the cumulative patch. Before freezing budgets, measure T1/T2 wall-clock on 3 representative repos (small Python, medium TypeScript, large monorepo). Swap `pylint` → `ruff`-only in T2; demote `tsc --noEmit` to T3/background unless a warm `tsc --watch` / `tsserver` is available. Run oracles in strict latency tiers within the shared 2.5 s budget:
   - **T0 — Cheap checks (< 5 ms total):** contract, lang-lock, kill-switch, symbolic rule engine.
   - **T1 — Syntactic:** `py_compile`, parser smoke — budget 500 ms.
   - **T2 — Import/static:** import-graph check, forbidden-import check, `ruff` on changed files — budget 1 s.
   - **T3 — Targeted test / `tsc` (manifest-cache only):** run only test files explicitly touched or named in the contract, **only if** a pre-computed repo manifest cache exists and remaining budget allows. Budget: remaining shared budget (best-effort; advisory-only in practice).
   - **T4 — Full test / build:** never blocks; runs post-turn in background.
   On any tier timeout, emit `signal_source="tier_timeout"`, treat the tier as advisory, and continue — never silent fail-open.
3. **Oracle aggregator.** Combine oracle results into a structured `OracleReport` with severity and file/line annotations.
4. **Recovery hint generator.** Use the local Qwen 1.5B critic (`src/gen_client.py`) to translate `OracleReport` + `ContractViolation` into a one-paragraph recovery hint, **only when running on GPU/MLX or when `RC_GEN_FAST=1` is set**. On CPU default hardware, fall back to template-based hints derived from the `OracleReport`. Cap the generative path at `RC_GEN_BUDGET_MS=1500`.
5. **Server-side loop.** On block, run the oracles, generate the hint, and surface it to the agent. Do not expose the loop to the agent; it sees one blocking stderr with a concrete fix direction.
6. **Latency budget.** End-to-end gate latency p95 < 2.5 s. PRM and oracles share this budget: run contract/lang-lock first, then PRM and oracles in parallel where possible, with each component capped by the remaining budget. T2 is best-effort and skipped if it would exceed the shared budget.

**Expected effect:** −30% post-edit test/lint failures, −20% token waste from retry loops.

**Kill criteria:** If oracle false-positive rate > 25% on a 100-edit labeled set, disable auto-block and keep oracles as advisory.

### Phase 3 — Calibrated Process Reward Model gate (weeks 5–8)

Train and deploy a PRM that scores `(plan_claim, diff_hunk, contract_clause)` triples on a calibrated 0–1 scale.

**Deliverables:**
1. **Corpus.** Use `eval/build_prm_corpus.py` and `eval/calibration_corpus.py --include-positives` to build a labeled set of ~2,000 (plan_claim, diff_hunk, contract_clause, label) triples from:
   - Iter-3 grounding pairs.
   - reasoning-core's own git history (reverts/fixes as negatives, CI-passing commits as positives).
   - Synthetic scope-creep and refactoring fixtures.
2. **Prompt-based baseline first.** Before any fine-tuning, evaluate a prompt-based classifier using the local Qwen 1.5B model with a carefully crafted few-shot prompt on the full 2,000-triple corpus. Measure precision/recall and calibration error on a **human-verified subset of 300 triples** (not the full heuristic-labeled corpus). Proceed to fine-tuning only if prompt-based precision is already ≥ 0.60 on the human-verified set.
3. **Model (if baseline justifies it).** Fine-tune Qwen2.5-Coder-1.5B locally via MLX (or Scaleway for users who opt in) using **LoRA-scale adaptation**, not full fine-tuning, on the training split to output a calibrated probability that the diff honors the plan claim and contract.
4. **Calibration.** Use isotonic regression or Platt scaling with a held-out set of at least 1,000 samples (or 5-fold cross-validation if the corpus is smaller). Target calibration error < 0.05 on the calibration set.
5. **Deployment.** Wire `gate_prm` into `pre_edit_guard.py`. Block when PRM score < `RC_PRM_THRESHOLD` (default 0.25, calibrated per-repo). Emit `signal_source="prm"` audit events.
6. **Shadow mode first.** Run PRM gate in shadow until **both** conditions are met: (a) at least 2 weeks of production traffic, and (b) at least 1,000 shadow events collected across **≥5 distinct repo installs** (use the maintainer's own repositories: reasoning-core, ctf-llm-infra, and 3+ other active local projects). Only then flip to block.

**Expected effect:** PRM-driven block precision ≥ 0.75 on held-out plan/diff pairs; replaces the degenerate `coherence_delta` signal.

**Kill criteria:** If prompt-based PRM precision < 0.60 on the full corpus, **do not fine-tune** — rely on contract compiler + execution oracles. If fine-tuned PRM precision < 0.60 after 4 weeks of shadow, revert.

### Phase 4 — Self-improving calibration from git history (weeks 8–12)

Make the guardrail learn from the repo it protects.

**Deliverables:**
1. **Commit miner.** Nightly, scan the last N commits on the current branch. Label commits using a defined heuristic:
   - **Positive:** survived to `HEAD` with no follow-up commit within 48 hours touching the same files and with message NOT matching `fix|revert|hotfix|patch`.
   - **Negative:** followed within 48 hours by a commit touching the same files where either (a) the later message matches `fix|revert|hotfix|patch`, or (b) the later commit's diff reverts >30% of the earlier commit's changed lines.
   Validate the heuristic on a human-labeled set of 100 commits before using it for threshold promotion. Report κ between heuristic and human labels.
2. **Feature extraction.** For each labeled commit, extract: contract violations, oracle results, PRM score, risk vector, edit metadata.
3. **Threshold recalibration.** Retrain per-kind thresholds and PRM calibration weekly. Require κ ≥ 0.6 between automated labels and a human spot-check of 50 samples before promoting new thresholds.
4. **Promotion policy.** New thresholds run in shadow for 48 hours; only then can they flip to enforce.
5. **`rc audit-history` subcommand.** Show the last N commits and what the gate would have done — useful for TTFV and trust.

**Expected effect:** Threshold drift reduced; block precision improves 10–15% over 3 months of runtime.

**Kill criteria:** If automated labels disagree with human spot-check (κ < 0.5) for two consecutive weeks, pause recalibration.

### Corpus & labeling workstream (parallel, weeks 1–12)

The plan's kill criteria depend on labeled data; without an explicit labeling plan, every kill criterion risks being skipped for lack of labels.

| Phase | Artifact | Count | Source | Hours estimate | Owner |
|---|---|---:|---|---:|---|
| 0 | Hand-labeled random-mamba replay events | 200 | Existing 68,794-event audit corpus + regression fixtures | 4 | engineer |
| 1 | Hand-annotated `PLAN.md` contract extractions | 50 | reasoning-core repo + 10–15 popular OSS repos with READMEs/issues; **at least 15 plans must be written by someone other than the parser author** (or real community PLAN.md/design docs verbatim) | 8 | engineer |
| 2 | Labeled oracle true/false-positive edits | 100 | reasoning-core history + synthetic fixtures | 6 | engineer |
| 3 | PRM triples `(plan_claim, diff_hunk, contract_clause, label)` | ~2,000 | Iter-3 grounding pairs + reasoning-core git history + synthetic scope-creep/refactor fixtures | 16 | engineer + scripted |
| 3 | Calibration holdout | ≥1,000 | Subset of PRM triples | 0 (held out) | — |
| 4 | Human-labeled commit good/bad set | 100 | reasoning-core + 2–3 other active local repos | 4 | engineer |
| 4 | Weekly spot-check samples | 50 | Recent commits | 2/week | engineer |

**F1 split by source:** Report contract-extraction F1 separately for in-distribution (author-written) plans and out-of-distribution (community/team-written) plans. The combined F1 must still meet the gate, but the out-of-distribution F1 drives the go/no-go decision.

**Synthetic fixture strategy:** Generate scope-creep, refactoring, import-violation, and test-omission examples from reasoning-core's own source tree using deterministic mutations. This covers the long tail without waiting for rare real events.

---

## 5. Architecture changes

### 5.1 New data flow and signal ordering

```
Claude proposes Edit/Write
        │
        ▼
┌──────────────────────────────────────────┐
│ pre_edit_guard.py                        │
│  T0 — Cheap checks (< 5 ms)              │
│    1. Contract compiler check            │
│    2. Lang-lock / kill-switch            │
│    3. Symbolic rule engine               │
│  Shared budget stage (≤ 2.5 s total)     │
│    4. PRM score (local Qwen 1.5B)        │  GPU/MLX only; else skipped
│    5. Execution oracles (scratch)        │  T1/T2 always; T3 best-effort
└─────────────┬────────────────────────────┘
              │
              ▼
        Allow / Block
              │
              ▼
        Audit log + recovery hint
```

Scratch clones live in `$RC_CACHE_DIR/rc-scratch/<session_key>/` (default `~/.cache/reasoning-core/rc-scratch/`, mode `0700`), not `/tmp`.

**Ordering invariant:** T0 cheap checks run first and can short-circuit. PRM and oracles run within a shared 2.5 s budget (parallel where possible). When signals disagree (e.g., PRM says safe but oracle says syntax error), the **most severe signal wins**: oracle failure overrides PRM safety. The recovery hint names the layer that fired.

The SSM sidecar decision is made in Phase 0: either it is validated as a useful input to the PRM/project-index features and kept, or it is removed from the hot path entirely. It is not retained as an "optional backend" without a concrete, validated use case. If kept, it no longer blocks the hot path.

### 5.2 What to remove

- The 11-dim risk vector marketing. Ship an 8-dim vector honestly, or drop the vector entirely in favor of PRM + contract + oracle signals.
- `coherence_delta` as a primary block signal. Keep it as a logged diagnostic only.
- `architectural_impact_score` claims until it is computed and emitted.
- Dead gates in `_dispatch.py` that are not wired.
- Mixed `S2_HARD_CAP_MS` defaults; standardize on 1500 ms with documented symbolic fallback.

### 5.3 What to keep

- Loopback-only sidecar (`127.0.0.1`).
- Repo-scoped `direnv` activation.
- JSONL audit log with `gate_id`, `signal_source`, `latency_ms`, `retry_after_block`.
- PreToolUse hook enforcement point.
- Multi-CLI support (Claude, Gemini, Copilot, Vibe) via hooks + MCP bridge.

---

## 6. Success metrics and eval protocol

### 6.1 North-star metric

Continue using the `agent_reasoning_efficiency` composite from `2026-06-01-reasoning-core-1000pct-improvements.md` §7, but redefine the numerator to count only reasoning-quality true positives:

```
agent_reasoning_efficiency =
    (net_reasoning_quality_blocks - operator_overrides) / (gate_wall_clock_s + 1)
        × repo_idiom_adherence_delta_norm
        × (1 − component_unavailability_rate)
```

where:
- `net_reasoning_quality_blocks` excludes shell-escape, guard-file, and lang-lock self-protection blocks.
- `repo_idiom_adherence_delta_norm` = judge-rated repo-fit delta (1–5 BARS scale) normalized to [0, 1] and clamped to a floor of 0.5 so measurement noise cannot zero out the metric.
- `component_unavailability_rate` = fraction of `/score` calls that failed-open or timed-out across the active components in that measurement period.

**Target:** Targets are set after a 20-task pilot in Phase 1. Working hypothesis: 2–3× improvement in `agent_reasoning_efficiency` and a 5–10 pp CTS lift, with a stretch goal of +15 pp if the pilot supports it. The 10×/100× aspirational targets are retired until empirical evidence justifies them.

### 6.2 Primary eval: Clean Task Success (CTS)

A task succeeds iff:
- All `fail_to_pass` tests pass.
- No previously passing test (`pass_to_pass`) fails.
- The set of source files touched is sufficiently aligned with the gold-patch source files: edit-set Jaccard ≥ 0.8 **and** any files outside the gold patch are either (a) test/fixture files explicitly allowed by a contract-independent whitelist applied symmetrically to both control and treatment, or (b) explicitly named in the **frozen contract** applied to both arms. Agent-authored contract expansion counts as scope creep.

The frozen contract is derived from the SWE-bench issue text before the agent runs and is applied to both control and treatment scoring. This keeps CTS and the scope-creep reduction metric aligned: an edit that expands scope without pre-frozen contract authorization fails both CTS and ΔSCR.

**Protocol:**
- **Contracts are frozen before the agent runs.** For the pilot, contracts may be hand-authored from the SWE-bench issue text. For the main eval, contracts must be derived by the same parser pipeline from the issue text, with no hand-tuning per task. State explicitly in the eval report which path was used and keep pilot/main consistent.
- **Pilot (Phase 1):** n = 20 paired SWE-bench Verified Python tasks to estimate the CTS lift distribution and set realistic targets.
- **Main eval (week 12):** n = 150 paired tasks (300 Claude runs), stratified by gold-patch file count, if the pilot justifies it.
- Control: vanilla Claude Code.
- Treatment: upgraded reasoning-core with contract compiler + execution oracles + PRM gate enabled.
- Ablation: contract + oracles only, no PRM/SSM, to isolate the marginal value of the neural/PRM components.
- Statistical test: paired two-sided Wilcoxon signed-rank, Holm-corrected across secondary metrics.

**Target:** ≥ +5 pp CTS lift vs control after pilot; escalate to +10–15 pp only if pilot data supports it.

**Power and decision rule:** Before the main eval, run a power calculation using the discordance rate observed in the 20-task pilot. If n=150 is underpowered for a +5 pp lift at α=0.05, increase n or relax the decision criterion to: **point estimate ≥ +3 pp and the 95% CI excludes 0**. Pre-register the chosen rule before running the main eval.

### 6.3 Secondary metrics

- Regression Rate, Resolved Rate, Scope-Creep Rate.
- Hook false-positive block rate (FPBR), true-positive block rate (TPBR), block recovery rate (BRR).
- Gate wall-clock p50/p95 per task.
- Token cost per task.
- Sidecar fail-open / timeout rate.
- Signal-source decomposition.
- Operator override survival ratio.

### 6.4 Kill criteria

Stop the upgrade if any of the following hold at the 90-day eval:
- CTS Δ < 0.03 or the 95% CI includes 0 (using the pre-registered rule from §6.2).
- Regression Rate harm > 5 pp.
- FPBR > 0.20.
- BRR < 0.50.
- Median wall-clock ratio > 1.5× vs vanilla.
- Fail-open / timeout rate > 10% of `/score` calls.
- Scope-creep reduction ΔSCR < 0.05.
- ≥50% of CTS lift comes from self-protection blocks rather than reasoning-quality blocks.

---

## 7. Why this is a game changer

Current AI coding agents (Claude Code, Cursor, Copilot, Codex CLI) all suffer from the same failure mode: the model proposes edits that are linguistically plausible but structurally wrong, and the only enforcement available is post-hoc review by the human. reasoning-core can become the first product that:

1. **Makes the plan enforceable.** The agent's plan is compiled into a contract that is checked on every edit, not treated as polite advice.
2. **Catches errors before disk.** Execution oracles in a scratch clone verify syntax, imports, tests, and invariants before the user's repo is touched.
3. **Learns from the repo.** Thresholds and PRM calibration improve nightly from the repo's own git history, not from a one-size-fits-all benchmark.
4. **Stays local and portable.** Unlike Cursor's IDE-integrated classifier or Copilot's cloud policy layer, reasoning-core is editor/LLM-agnostic and keeps code on the user's machine.

That combination does not exist in the market today. If executed, reasoning-core stops being a faster linter and becomes a structural-reasoning layer that makes AI coding agents reliable enough to run unsupervised.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Contract extraction is brittle | Start with structured markdown; LLM fallback with confidence; keep warn-only until F1 ≥ 0.80 (split by in/out-of-distribution) |
| Claim↔hunk alignment fails | Path overlap only for blocking; embedding alignment advisory; default warn-only if not met |
| Execution oracles are too slow | Tiered oracles within shared 2.5 s budget; persistent worktree; T3 best-effort |
| Scratch-clone oracles false-positive on intermediate edits | Apply oracles to cumulative patch, not isolated hunks; stable session key |
| PRM precision is low | Prompt-based baseline first; fine-tune only if baseline precision ≥ 0.60; 2-week / 1,000-event shadow |
| PRM not ready for week-12 eval | Pre-register PRM as ablation in main eval; second eval wave if promotion gate clears |
| Calibration set too small | Use ≥1,000-sample holdout or 5-fold cross-validation |
| Staffing bottleneck | Plan requires ≥2 engineers; extend timeline or drop fine-tuning if single-engineer |
| False positives frustrate users | Default advise mode; flip to copilot only after Phase 2 kill criteria pass; easy `rc bypass-next` |
| Audit precision ground truth missing | `rc bypass-next` / `rc confirm-next` emit explicit override/confirmation audit events |
| Git-history labels are noisy | Require κ ≥ 0.6 vs human spot-check; 48-hour shadow before threshold promotion |
| SSM embedder becomes irrelevant | Offline `random-mamba` control in Phase 0; binary keep/remove decision |
| Competitive vendors catch up | Ship local audit and portability as core differentiators; avoid cloud-only features |

---

## 9. Staffing and timeline

This plan assumes **≥2 engineers** for the 90-day timeline. With one engineer, extend Phase 2–4 by 4–6 weeks or drop the PRM fine-tuning path (keep prompt-based PRM only).

### Immediate next steps

1. **This week:** Open Phase-0 honesty patch PR (doc alignment, dead-code burial, real symbolic fallback, offline `random-mamba` control, override/confirmation CLI signals).
2. **Week 2:** Merge Phase 0; begin Plan-to-Contract compiler schema and parser.
3. **Week 4:** Contract schema v1.0 freeze; ship contract compiler in shadow mode on reasoning-core repo; run 20-task SWE-bench pilot to set realistic targets.
4. **Week 5:** Begin execution-oracle MVP with T0–T2 tiers and cumulative patch tracker.
5. **Week 6:** Start PRM corpus labeling and prompt-based baseline evaluation.
6. **Week 8:** Integrate prompt-based PRM in shadow. Fine-tuning only if baseline justifies it and timeline allows.
7. **Week 12:** Run main eval against SWE-bench Verified with treatment = **contract compiler + execution oracles**. PRM is a pre-registered ablation if it has not yet cleared its ≥1,000-event / ≥2-week promotion gate. If PRM is ready, run a second eval wave (week 14–16) comparing contract+oracles vs contract+oracles+PRM.
8. **Week 16:** Decide ship/kill based on both eval waves.

---

## References

- `README.md`, `docs/PLAN.md`, `docs/HOW_IT_WORKS.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/ROADMAP.md`
- `src/s2_core.py`, `src/ssm_backbone.py`, `src/hooks/_dispatch.py`, `src/hooks/pre_edit_guard.py`, `src/gen_client.py`, `src/mcp_reasoner.py`, `src/project_index.py`, `src/rc_cli.py`
- `thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md`
- `thoughts/shared/research/2026-06-02-community-pain-points.md`
- SOTA: AgentPRM (arXiv:2511.08325), FunPRM (arXiv:2601.22249), ThinkPRM (arXiv:2504.16828), ToolPRMBench (arXiv:2601.12294), SAGE (arXiv:2603.15255), CodeRAG/GraphCodeAgent (arXiv:2504.10046)
