---
date: 2026-07-16
status: Phases 0-3 implemented; full n=100 eval pending
author: reasoning-core analysis swarm
adversarial_review:
  - 2026-07-16 — adversarial reviewer (claude-fable-5), verdict "FIX-FIRST"
  - 2026-07-16 — speed/review engineer, verdict "compress and parallelize"
  - 2026-07-16 — skeptical researcher, verdict "not falsifiable without fixes"
  - 2026-07-16 — security adversary, verdict "guard integrity missing"
  - 2026-07-16 — similar-tools research (Aider, CodeGate, Guardrails, OPA, pyan3, Langfuse, SWE-bench)
  - 2026-07-16 — Phase 0 security review (BLOCKER: guard-hash auto-register, operator auth, reconcile path)
  - 2026-07-16 — Phase 0 QA review (BLOCKER: env leak, in-plan test assertion, audit assertions)
  - 2026-07-16 — Phase 1 QA review (BLOCKER: env scrubbing, plan format, audit assertions)
supersedes: thoughts/shared/plans/2026-07-09-reasoning-core-game-changer-upgrade.md
---

# reasoning-core Restoration Plan — From Shadow Tripwire to Structural Copilot (Rev 1)

## Implementation status (as of 2026-07-16)

| Phase | Status | Key deliverables |
|---|---|---|
| Phase 0 — Honesty, wiring, guard integrity | **Done** | README honest defaults, authenticated `rc enable/disable-enforcement` with fenced markers, `rc guard-hash` (no trust-on-first-use), `rc reconcile`, test quarantine |
| Phase 1 — Make copilot fire safely | **Done** | Plan-to-contract compiler (already in `_plan_contract.py`), staged profile (Stage 1 warn, Stage 2 hard), block UX, staged enforcement pilot tests |
| Phase 2 — Falsifiable evaluation | **Protocol done, n=5 sub-pilot done, full n=100 eval pending** | `docs/EVAL_PROTOCOL.md` with pre-registered primary endpoint, n=5 sub-pilot (operational smoke test, not the protocol's n=20 per arm) with operational kill criteria |
| Phase 3 — Post-decision hardening | **Partial** | Stop hook reconcile integration (`stop_reconcile.py`) for MCP-skip detection; optional integrations (Langfuse, Guardrails, Aider) deferred until funded |

## Remaining work

1. **Run the full n=100 SWE-bench Verified eval** per `docs/EVAL_PROTOCOL.md` §7. Requires the pilot to pass acceptance criteria first.
2. **Label training set** (10 examples per label) and train the two labelers.
3. **Wire `rc reconcile` as a mandatory pre-commit hook** in copilot mode (currently it's a Stop hook; pre-commit is optional and bypassable).
4. **Expand guard-hash coverage** to include `_kill_switches.py`, `_magic_comments.py`, `_guard_paths.py`, `_rule_engine.py`, `audit_log.py`.
5. **HMAC-protect the guard-hash store** against tampering by a same-user attacker.
6. **Implement `rc init-plan`** to scaffold a `PLAN.md` template (currently the error message references it but it doesn't exist).
7. **Update `docs/HARDENING.md`** to reflect the new authenticated enable/disable, fenced markers, and guard-hash flow.

## Executive summary

reasoning-core is a technically impressive local enforcement layer that, as shipped, defaults to a **symbolic/self-protection tripwire in shadow mode**. The docs promise a neural structural-reasoning copilot; the code delivers audit, warn, and dead-gate collection. This plan closes the gap between marketing and mechanics, then runs a falsifiable evaluation that either proves the value or forces an honest rollback.

**Primary bet:** Make the enforcement defaults honest, make the opt-in enforcement path safe, and validate it against a **human-labeled reasoning-quality failure endpoint** and SWE-bench Verified. If the effect does not appear, copilot mode is removed from the codebase, not just left opt-in.

**Pre-registered kill criterion:** If the treatment arm does not show a ≥15 pp reduction in the primary endpoint (reasoning-quality failure rate) with Holm-corrected p < 0.05, or if the fail-closed operational kill criteria fire, the copilot enforcement profile is removed and reasoning-core returns to a documented advisory-only tool.

**Definition of done:**
1. README/install defaults are consistent and honest about the advisory-only default.
2. `rc enable-enforcement` is authenticated, staged, and produces safe, measurable reasoning-quality blocks without bricking the editor.
3. The 11-dim risk vector is no longer marketed; docs describe the 8-dim default and the three optional dims as requiring `/baseline` + `RC_PROJECT_INDEX=1`.
4. The eval runs in the recommended production configuration (`S2_FAIL_CLOSED=1`), with ablation arms, and publishes a decision.

---

## 1. Current reality (from the 2026-07-16 audit)

| Fact | Source | Severity |
|---|---|---|
| Installed defaults: `RC_MODE=advise`, `RC_SHADOW_MODE=1`, `RC_PLAN_BLOCK=0`, `RC_ORACLE_BLOCK` unset, `S2_FAIL_CLOSED=0` in `.envrc.local` | `install.sh:154-172`, `.envrc:83-119` | Critical |
| Only 4.1% of production hard blocks were reasoning-quality blocks | `docs/AUDIT_GAP_2026_07_10.md` | Critical |
| 87.7% of SSM-tagged events fail-open/closed | `docs/AUDIT_GAP_2026_07_10.md` | Critical |
| 11-dim risk vector appears in 8 of 68,794 events; `architectural_impact_score` in 0 | `docs/AUDIT_GAP_2026_07_10.md` | Major |
| `pre_edit_guard.py` never POSTs `/baseline`, so Phase-2 dims are zeros | `src/s2_core.py:1010-1048`, `pre_edit_guard.py` | Major |
| Copilot/Vibe are Tier-2 MCP-only gates, often skipped under pressure | `docs/CLI_PARITY.md` | Minor |
| Full SWE-bench eval never run; only stub smoke (n=2) | `docs/EVAL_RESULTS.md` | Major |
| Guard code/config live in the same shell as the agent | Security review | Critical |

### What this means

The product is not broken at the code level — every gate exists and has tests. It is broken at the **configuration, wiring, evidence, and guard integrity** level. The restoration work is mostly completion, hardening, and research discipline, not re-architecture.

---

## 2. Guiding principles

1. **Honesty before glory.** The README must not claim enforcement that the defaults do not perform.
2. **Opt-in, staged, authenticated.** Hard blocks require a deliberate operator action (`rc enable-enforcement`), the operator must authenticate, and the profile is staged so early adopters do not get a bricked editor.
3. **Kill what you cannot wire or validate.** Dead gates, unmeasured dimensions, and unvalidated claims are removed from marketing and code path.
4. **Evidence gates progress; no evidence means rollback.** No phase ships until its eval criteria are green. The pre-registered kill criterion is binding.
5. **Preserve local-only operation.** No cloud relay, no telemetry without opt-in, loopback-only sidecar. Guard integrity is added without breaking this.
6. **Guard integrity.** The agent and guard must not share the same writable namespace.

---

## 3. Phase 0 — Honesty, wiring, and guard integrity (week 1)

Phase 0 runs in parallel tracks: code/doc honesty, guard integrity, and eval-prep/pilot.

### 3.1 Align README with reality

**Deliverables:**
- Rewrite `README.md` to describe the **actual** default posture: local audit/warn-only guardrail that becomes a blocking copilot only after `rc enable-enforcement`.
- Remove all enforce-by-default, 11-dim-vector, and regression-reduction claims. Replace with: "Opt-in enforcement is under active validation; default mode is advisory."
- Update `docs/HOW_IT_WORKS.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md` to match current defaults.

**Acceptance criteria:**
- A reader who follows only the README cannot reasonably believe the gate blocks by default.
- Every claim about blocking or eval results is footnoted with the required activation step or eval status.

### 3.2 Wire the active gates; bury the dead ones

**Decision:** Keep the 8-dim risk vector as the default. Remove 11-dim marketing in this PR. The three optional dims (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are documented as requiring `/baseline` + `RC_PROJECT_INDEX=1`. A future plan can validate and wire them; this plan does not.

**Deliverables:**
- In `pre_edit_guard.py`, call `gate_rule_engine`, `gate_prm`, and `gate_plan_grounding` through `_dispatch.py` so they are not dead code.
- Add `gate_symbolic_fallback` that runs on `S2_HARD_CAP_MS` timeout with rule engine, lang lock, and plan grounding. SSM becomes an advisory severity input, not the blocking layer.
- If a gate remains default-off (e.g., PRM), document why and the activation conditions.

**Acceptance criteria:**
- `grep` shows active callers for `gate_rule_engine`, `gate_plan_grounding` in `pre_edit_guard.py`/`_dispatch.py`.
- Symbolic fallback tests cover `S2_FAIL_CLOSED=0` warn and `S2_FAIL_CLOSED=1` block.

### 3.3 Stage `rc enable-enforcement` with authentication and markers

**Security principle:** The agent and guard share the same shell, so enforcement state must be moved outside the agent's writable namespace. As an immediate fix, operator authentication and immutable config markers are required.

**Deliverables:**
- `rc enable-enforcement` requires operator authentication: TTY presence, OS keychain unlock, or a signed operator token.
- Write a fenced block into `.envrc.local` with `# >>> rc enforcement >>>` / `# <<< rc enforcement <<<` markers. `rc enable-enforcement` replaces between markers; `rc disable-enforcement` removes only the block.
- **Stage 1 profile (Phase 0, safe defaults):**
  ```bash
  export RC_MODE=copilot
  export RC_SHADOW_MODE=0
  export RC_PLAN_BLOCK=1
  export RC_PLAN_GROUNDING=1        # warn-only until contract compiler is validated
  export RC_ORACLE_BLOCK=1
  export RC_RULE_ENGINE=1
  export RC_PROJECT_INDEX=1          # enables optional dims if/when baseline wired
  export S2_FAIL_CLOSED=1
  ```
- `rc disable-enforcement` removes the block and reverts to advise/shadow.
- After writing, run `rc status` and compare active env against expected; fail if they differ.
- **Do not scaffold `PLAN.md` from README.** A fabricated plan is worse than no plan. Instead, refuse to enable enforcement until a `PLAN.md` exists or the operator runs `rc init-plan`.

**Acceptance criteria:**
- After `rc enable-enforcement`, `rc status` reports copilot mode with staged, safe defaults.
- `rc disable-enforcement` cleanly removes only the fenced block.
- Agent cannot run `rc enable-enforcement` without operator authentication.

### 3.4 Guard integrity — hardening

**Deliverables:**
- Add a `rc guard-hash` command that verifies `src/hooks/pre_edit_guard.py`, `_dispatch.py`, and the active `.envrc.local` enforcement block against stored hashes. If the guard files are modified, emit a high-severity audit event and fail closed.
- Ensure `.envrc.local` and guard files are not writable by the agent process if possible (e.g., set read-only bit after write; document macOS/Linux limits).
- Sidecar watchdog: if the sidecar is unresponsive, fail closed; require operator override to proceed.
- Redact secrets and PII from audit logs and eval artifacts by default; define retention.

**Acceptance criteria:**
- `rc guard-hash` detects tampering with guard files or config.
- Sidecar down + `S2_FAIL_CLOSED=1` → exit 2 with clear reason.

### 3.5 Ship `rc reconcile` safety net

**Deliverables:**
- Add `rc reconcile` that diffs `git status --porcelain` against `gate_edit` audit rows in the session.
- Flags any file written without a corresponding `gate_edit` call.
- Make reconciliation mandatory before any commit/push in copilot mode, or run it as a stop hook.

**Acceptance criteria:**
- `rc reconcile` catches an MCP-skip scenario in a test repo.

### 3.6 Stabilize tests and quarantine the slow ones

**Deliverables:**
- Quarantine `test_pre_plan_guard.py` and `test_baseline_drift.py` into a separate `slow` mark; add a `docs/TEST_QUARANTINE.md` with owner and target fix date.
- Fix or quarantine the two `test_sidecar_hard_cap.py` failures.
- Add a CI job that runs `pytest -m "not live and not slow"` with a 10-minute timeout and fails on new timeouts.
- Keep a separate `slow` CI job that runs the timeout-prone integration path and reports the failure rate, not just mock success.

**Acceptance criteria:**
- `pytest -m "not live and not slow" -q --timeout=60` passes fully on a clean checkout.
- No new tests are added to the non-quarantined suite unless they pass within the timeout.

### 3.7 Prep eval harness and start the n=20 pilot (parallel track)

**Deliverables:**
- Set up the SWE-bench Verified harness so the n=20 pilot can start in week 1 or 2.
- Define the primary endpoint and labeling protocol before any data is collected (see §5.2).
- Run the first 5–10 treatment runs to discover FPBR, BRR, and fail-closed timeout rates immediately.

---

## 4. Phase 1 — Make the copilot fire safely (week 2–3)

### 4.1 Plan-to-contract compiler (replacement for file-list grounding)

**Deliverables:**
- Derive a machine-readable contract from `PLAN.md` with:
  - `allowed_paths` (globs)
  - `forbidden_paths` (globs)
  - `required_tests` (test files that must exist or be touched)
- Verify `PLAN.md` hash before deriving the contract; require operator approval for any `PLAN.md` change in copilot mode.
- Block edits outside `allowed_paths` when `RC_PLAN_GROUNDING=2`.
- Warn (not block) on edits inside `allowed_paths` but not obviously described by the plan.
- Consider using **Pydantic** for contract schema validation and **OPA** (Open Policy Agent) as an optional policy evaluator for complex rules.

**Acceptance criteria:**
- 100% of edits outside the contract are blocked in copilot mode.
- False-positive rate on edits inside `allowed_paths` is <10% in a hand-labeled, blinded 50-edit sample with Cohen's κ ≥ 0.70 between two independent labelers.
- If FPBR > 25%, keep `RC_PLAN_GROUNDING=1` and do not proceed to Stage 2.

### 4.2 Promote plan-grounding to hard block (Stage 2 profile)

**Deliverables:**
- Once §4.1 acceptance criteria are met, `rc enable-enforcement --hard` writes the Stage 2 profile with `RC_PLAN_GROUNDING=2`.
- `rc enable-enforcement` (without `--hard`) continues to use Stage 1 (warn-only plan grounding) for safety.

**Acceptance criteria:**
- A treatment-arm run with Stage 2 profile blocks scope drift without mass false positives.

### 4.3 Make SSM advisory, not blocking

**Deliverables:**
- Do not profile the SSM for weeks. Ship `gate_symbolic_fallback` as the blocking path on `S2_HARD_CAP_MS` timeout.
- Keep SSM scoring as a severity input (e.g., raise the severity of a rule-engine/oracle/plan-grounding block).
- Measure SSM p50/p95 in a one-day experiment after symbolic fallback is stable; use it only to tune advisory severity, not blocking latency.
- Ensure `S2_FAIL_CLOSED=1` causes a hard block (symbolic fallback) on sidecar timeout.

**Acceptance criteria:**
- In copilot mode, sidecar timeout with `S2_FAIL_CLOSED=1` results in exit 2 and clear audit reason.
- `fail_open` events in copilot mode are <5% of all events under normal operation.
- p95 block latency < 5 s (not median). Median target remains <2 s.

### 4.4 Operator experience for eval

**Deliverables:**
- Move block-message and `rc explain` improvements from Phase 3 to Phase 1.
- Every hard block emits: decision-id, signal source (contract/rule/oracle/plan), specific clause/rule/oracle that fired, and override instruction.
- `rc explain <decision-id>` works for rule-engine, oracle, and plan-grounding blocks.

**Acceptance criteria:**
- An operator can read a block message and understand why the block happened without reading source code.

### 4.5 Structural analysis improvements

**Deliverables:**
- Evaluate replacing Python call-graph heuristics with **pyan3** or **astroid/jedi** for real call-graph and import-cycle detection.
- Add a spike comparing the current `build_call_graph` output against pyan3 on 3 representative repos.
- If pyan3 is better, integrate it behind a feature flag; if not, document why and keep the current parser.

**Acceptance criteria:**
- A decision record is written in `docs/adr/ADR-NNN-call-graph-backend.md`.

---

## 5. Phase 2 — Falsifiable evaluation (week 4–5)

### 5.1 Evaluation design

**Primary endpoint:** Reasoning-quality failure rate — the fraction of edited tasks whose final patch contains a scope drift, plan violation, or structural regression as judged by blinded human labelers. This is a **validated, human-labeled** endpoint, not SWE-bench test failure alone.

**Secondary endpoint:** SWE-bench Verified Clean Task Success (CTS) or regression rate, as a sanity check.

**Ablation arms (n=20 per arm in pilot, n=100 per arm in full eval):**
| Arm | Configuration | Purpose |
|---|---|---|
| A | Vanilla Claude Code | Baseline |
| B | Rule engine + oracle block only | Structural/syntactic baseline |
| C | Plan grounding only | Scope-enforcement baseline |
| D | Full copilot (rule + oracle + plan grounding) | Treatment |
| E | Current default mode (advise/shadow) | Product-as-shipped baseline |

**Operational configuration:** The primary eval runs in the recommended production configuration: `S2_FAIL_CLOSED=1`, `RC_MODE=copilot`, `RC_SHADOW_MODE=0`, `RC_PLAN_GROUNDING=2`, `RC_ORACLE_BLOCK=1`, `RC_RULE_ENGINE=1`. Timeout/fail-open rate is reported as a separate operational metric.

**Plan provenance for grounding:** For each task, the agent authors a `PLAN.md` from the issue text before editing. Grounding effect is reported as a separate ablation (Arm C vs. Arm B), not pooled into the primary treatment claim. This prevents the "gold-patch leak" or "no-op" failure modes.

### 5.2 Labeling protocol

- Two independent labelers, blind to arm assignment and system output.
- Label each final patch for: (a) scope drift, (b) plan violation, (c) structural regression, (d) syntax/type error, (e) test failure.
- Cohen's κ ≥ 0.70 required; if not reached, resolve rubric and re-label.
- Inter-rater reliability and per-labeler confusion matrices are reported.

### 5.3 Statistical design

- Use a **paired proportions test** (McNemar or exact paired test) for the primary endpoint, not a Wilcoxon/sign-test on binary data.
- Pre-register the hypothesis: treatment (Arm D) reduces reasoning-quality failure rate by ≥15 pp vs. Arm A, with Holm-corrected p < 0.05.
- Pre-register operational kill criteria:
  - >10% of treatment runs abort due to hard blocks or fail-open loops → halt eval and return to Phase 1.
  - FPBR > 25% in labeled sample → halt eval.
  - p95 block latency > 5 s → halt eval.
- Compute power via simulation for the paired-proportions test, not Cohen's h for independent proportions.

### 5.4 Decision table (revised)

| Outcome | Action |
|---|---|
| Primary endpoint reduction ≥15 pp, Holm p < 0.05, no operational kill criteria | Flip `install.sh` default to `RC_MODE=copilot` with first-run wizard; keep `autopilot` opt-in. |
| Primary endpoint reduction 5–15 pp, Holm p < 0.05, no operational kill criteria | Keep opt-in; claim measured effect only; document required sample size for future re-run. |
| Primary endpoint reduction 5–15 pp, not statistically significant | Keep opt-in; do not claim effect. |
| No primary endpoint reduction or worse | **Remove copilot mode from the codebase**; keep advisory-only tool. |
| Operational kill criteria triggered | Halt eval; return to Phase 1 tuning. If still failing after re-tune, remove copilot mode. |

### 5.5 Publish the eval report

**Deliverables:**
- Write `docs/EVAL_RESULTS-2026-08.md` with full methodology, raw metrics, decision table, ablation results, inter-rater reliability, and threats to validity.
- Update `README.md` and `BENCHMARKS.md` to reference the new report and state the outcome honestly.
- Archive redacted artifacts; do not publish raw transcripts or secrets.

---

## 6. Phase 3 — Post-decision hardening (week 6–8, if copilot survives)

### 6.1 Tier-2 CLI hardening

**Deliverables:**
- `rc reconcile` (already shipped in Phase 0) becomes mandatory before commit/push in copilot mode.
- Document that Copilot/Vibe are advisory-only and recommend Tier-1 hosts for mission-critical work.
- Improve `AGENTS.md` / `copilot-instructions.md` prompts to reduce MCP skip rate.

### 6.2 Operator experience

**Deliverables:**
- `rc explain` already supports all signal sources from Phase 1.
- Add `rc viz` to render a Mermaid sparkline of drift and block history.
- Add block recovery hints.

### 6.3 Optional integrations (deferred to Phase 3)

**Deliverables:**
- **Langfuse:** emit gate decisions as traces/observations for teams that opt in.
- **Guardrails AI:** wrap reasoning-core checks as reusable validators so any agent using Guardrails can call the gate.
- **Aider:** study and optionally port the linter/test-runner loop for post-gate verification.

**Non-deliverable:** pre-commit hook is cut from this plan. It is client-side, bypassable, and a distraction from the core problem.

---

## 7. Success metrics and kill criteria

### 7.1 Per-phase success gates

| Phase | Gate | Instrument | Minimum | Kill if not met |
|---|---|---|---|---|
| 0 | README/defaults consistent | README review | No enforce-by-default claims | Halt |
| 0 | Tests pass (non-quarantined) | CI | `pytest -m "not live and not slow"` green | Halt |
| 0 | `rc enable-enforcement` authenticated | Manual test | Requires TTY/keychain/signed token | Halt |
| 1 | Plan contract FPBR < 25% | Blinded 50-edit sample, κ ≥ 0.70 | FPBR < 0.25 | Keep grounding warn-only |
| 1 | Symbolic fallback blocks cleanly | CI test | `S2_FAIL_CLOSED=1` timeout → exit 2 | Halt |
| 1 | p95 block latency < 5 s | Audit log | p95 < 5000 ms | Halt |
| 2 | n=100 eval completes | SWE-bench harness | 200 runs coded or abort-coded | Do not claim |
| 2 | Primary endpoint ≥15 pp, Holm p < 0.05 | Blinded labels | Δ ≥ 0.15 | Remove copilot mode |
| 2 | Operational kill criteria | Audit log | <10% abort, FPBR < 25%, p95 < 5 s | Halt and re-tune |

### 7.2 North-star metrics (revised)

| Metric | Target | Measurement | Why |
|---|---|---|---|
| Reasoning-quality precision | ≥0.70 | Blinded labeled sample of blocks | Validates blocks are actually catching reasoning-quality issues |
| Reasoning-quality recall | ≥0.50 | Blinded labeled sample of regressive edits | Validates gate does not miss real issues |
| True FP rate | <0.25 | Fraction of blocked edits that, when replayed, do not cause test/plan failure | Better than composition ratio |
| Recovery value | Net improvement in final patch vs. first-blocked patch | Human label | Blocks must lead to better code, not just retries |
| Fail-open rate | <5% | Audit events in copilot mode | Operational correctness |
| p95 block latency | <5 s | `hook_duration_ms` | Tail matters more than median |
| Primary endpoint reduction | ≥15 pp | n=100 paired eval | Falsifiable value claim |

---

## 8. Risks and mitigations (revised)

| Risk | Mitigation |
|---|---|
| Agent disables enforcement via env vars | Move enforcement state to authenticated config + guard hash check; require operator token for disable. |
| Agent edits guard files or `PLAN.md` | `rc guard-hash` verifies file integrity; `PLAN.md` hash checked before contract derivation; require operator approval for changes. |
| SSM sidecar too slow for real-time blocking | Symbolic gates are the blocking layer; SSM is advisory severity only. |
| Plan-grounding blocks too many legitimate edits | Stage with warn-only; promote to hard block only after 50-edit FPBR < 25%. |
| Eval cost/time not available | Run n=20 pilot first; abort if unpromising. |
| Copilot/Vibe MCP skip rate makes treatment look worse | Analyze Tier-2 separately; do not pool with Tier-1. |
| Threshold tuning overfits to SWE-bench | Freeze thresholds before n=100; label training data disjoint from confirmatory set; add generalization to threats. |
| README gets too conservative | Separate "What it does today" (advisory) and "What you can enable" (copilot) sections. |
| Audit/eval artifacts leak secrets | Redact by default; encrypt at rest; define retention; never publish raw transcripts. |
| Statistical incoherence | Use paired-proportions test; simulation-based power; pre-register. |

---

## 9. 5-week timeline (compressed)

| Week | Track A: Code | Track B: Eval | Deliverables |
|---|---|---|---|
| 1 | README/doc honesty, dead-gate wiring, authenticated `rc enable-enforcement`, `rc guard-hash`, `rc reconcile`, quarantine slow tests | Set up SWE-bench harness; define primary endpoint and labeling protocol; start 5–10 treatment runs | Phase 0 complete; first FPBR/BRR data |
| 2 | Plan-to-contract compiler, symbolic fallback as blocking layer, block UX/`rc explain` | Run n=20 pilot across all 5 arms; blind-label; measure FPBR, BRR, latency, abort rate | Phase 1 go/no-go |
| 3 | Promote plan-grounding to hard block if FPBR < 25%; integrate pyan3/OPA spike if time | Finalize thresholds; lock protocol; pre-register n=100 | Stage 2 profile ready |
| 4 | Hardening from pilot findings | Run n=100 full eval | Raw eval data complete |
| 5 | Apply decision table; remove copilot if kill criterion fires | Write report; publish redacted artifacts | Decision and report |

---

## 10. Immediate next steps (this session)

1. Open a PR for the README/install honesty patch + 11-dim vector doc fix.
2. Add `# >>> rc enforcement >>>` markers and `rc disable-enforcement`.
3. Quarantine slow tests and fix the two `test_sidecar_hard_cap.py` failures.
4. Write the primary endpoint rubric and labeling protocol in `docs/EVAL_PROTOCOL.md` before collecting any data.
5. Run 5–10 treatment runs with the staged Stage 1 profile to discover the fail-closed timeout rate immediately.
6. Do not scaffold `PLAN.md` from README during `rc enable-enforcement`; require `rc init-plan` or manual `PLAN.md`.
