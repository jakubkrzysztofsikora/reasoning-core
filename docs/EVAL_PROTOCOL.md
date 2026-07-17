# reasoning-core Evaluation Protocol (Phase 2)

> Pre-registered before data collection. Any change to the primary endpoint,
> labeling rubric, or decision table after the first run invalidates the
> evaluation.

## 1. Primary endpoint

**Reasoning-quality failure rate** — the fraction of edited tasks whose final
patch contains a scope drift, plan violation, or structural regression as
judged by blinded human labelers.

This is a **validated, human-labeled** endpoint, not SWE-bench test failure
alone. SWE-bench test failure measures "did the patch work," not "did the
agent reason well." A patch can pass tests while introducing scope drift or
plan violations; reasoning-core's value is preventing the latter.

### Labeling rubric

For each final patch, labelers answer (yes/no) for each of:

| Label | Question |
|---|---|
| `scope_drift` | Does the patch modify files outside the task's required scope? |
| `plan_violation` | Does the patch violate an explicit constraint from the issue or `PLAN.md`? |
| `structural_regression` | Does the patch increase coupling, reduce cohesion, or break an architectural invariant? |
| `syntax_type_error` | Does the patch introduce a syntax or type error? |
| `test_failure` | Does the patch cause a test failure? |

A task is labeled `reasoning_quality_failure = True` if ANY of
`scope_drift`, `plan_violation`, or `structural_regression` is True.

### Secondary endpoint

SWE-bench Verified Clean Task Success (CTS) and regression rate, as a sanity
check. This is reported alongside the primary endpoint but is NOT the basis
for the kill criterion.

---

## 2. Ablation arms

Five arms, each with n=20 in the pilot and n=100 in the full eval:

| Arm | Configuration | Purpose |
|---|---|---|
| A | Vanilla Claude Code (no hooks) | Baseline |
| B | Rule engine + oracle block only | Structural/syntactic baseline |
| C | Plan grounding only | Scope-enforcement baseline |
| D | Full copilot (rule + oracle + plan grounding, fail-closed) | Treatment |
| E | Current default mode (advise/shadow) | Product-as-shipped baseline |

**Operational configuration for the full eval** uses the recommended production
posture: `S2_FAIL_CLOSED=1`, `RC_MODE=copilot`, `RC_SHADOW_MODE=0`,
`RC_PLAN_GROUNDING=2`, `RC_ORACLE_BLOCK=1`, `RC_RULE_ENGINE=1`. Timeout/fail-open
rate is reported as a separate operational metric.

### Plan provenance for grounding

For each task, the agent authors a `PLAN.md` from the issue text before
editing. Grounding effect is reported as a separate ablation (Arm C vs. Arm B),
not pooled into the primary treatment claim. This prevents the "gold-patch
leak" or "no-op" failure modes.

---

## 3. Labeling protocol

- Two independent labelers, blind to arm assignment and system output.
- Labelers see only the final patch and the issue text (no transcript).
- Cohen's κ ≥ 0.70 required across the five labels; if not reached, resolve
  rubric and re-label.
- Inter-rater reliability and per-labeler confusion matrices are reported.
- A third adjudicator resolves disagreements.

### Training set

- 10 labeled examples per label (5 positive, 5 negative) provided to each
  labeler before the eval.
- Labelers must achieve κ ≥ 0.70 on the training set before labeling the eval.

---

## 4. Statistical design

- Use a **paired proportions test** (McNemar or exact paired test) for the
  primary endpoint, not a Wilcoxon/sign-test on binary data.
- Pre-registered hypothesis: treatment (Arm D) reduces reasoning-quality
  failure rate by ≥15 pp vs. Arm A, with Holm-corrected p < 0.05.
- Pre-registered operational kill criteria:
  - >10% of treatment runs abort due to hard blocks or fail-open loops →
    halt eval and return to Phase 1.
  - FPBR > 25% in labeled sample → halt eval.
  - p95 block latency > 5 s → halt eval.
- Compute power via simulation for the paired-proportions test, not Cohen's
  h for independent proportions.
- Report Holm-corrected p-values across all five arms vs. baseline.

---

## 5. Decision table (pre-registered)

| Outcome | Action |
|---|---|
| Primary endpoint reduction ≥15 pp, Holm p < 0.05, no operational kill criteria | Flip `install.sh` default to `RC_MODE=copilot` with first-run wizard; keep `autopilot` opt-in. |
| Primary endpoint reduction 5–15 pp, Holm p < 0.05, no operational kill criteria | Keep opt-in; claim measured effect only; document required sample size for future re-run. |
| Primary endpoint reduction 5–15 pp, not statistically significant | Keep opt-in; do not claim effect. |
| No primary endpoint reduction or worse | **Remove copilot mode from the codebase**; keep advisory-only tool. |
| Operational kill criteria triggered | Halt eval; return to Phase 1 tuning. If still failing after re-tune, remove copilot mode. |

---

## 6. Pilot run (n=20 per arm)

Before the full n=100 eval, run a pilot to:
1. Verify the harness works end-to-end.
2. Measure FPBR, BRR, and p95 block latency.
3. Tune thresholds and confirm the kill criteria are reachable.
4. Estimate effect size for the full eval power calculation.

### Pilot acceptance criteria

- All 100 pilot runs complete (5 arms × 20 tasks).
- Labelers achieve κ ≥ 0.70 on the training set.
- FPBR < 25% in the labeled sample.
- p95 block latency < 5 s.
- No more than 2/100 runs abort due to hard blocks.

If the pilot fails any criterion, return to Phase 1 tuning before the full eval.

---

## 7. Full eval (n=100 per arm)

- Run after the pilot passes.
- Capture all artifacts: `claude_transcript.jsonl`, `hook_events.jsonl`,
  `sidecar.log`, `test_results.json`, `per_task_metrics.json`.
- Redact secrets and PII before archiving.
- Apply the pre-registered decision table.

---

## 8. Threats to validity

- **Threshold overfitting**: Thresholds tuned on the pilot may not generalize.
  Freeze thresholds before the full eval; label training data disjoint from
  confirmatory set.
- **SWE-bench representativeness**: The benchmark is a proxy for real code
  editing. Generalization beyond SWE-bench is not claimed.
- **Labeler bias**: Two labelers reduce individual bias but cannot eliminate
  it. A third adjudicator resolves disagreements.
- **Agent version pinning**: The agent version must be pinned. Future
  re-runs must re-validate against the current agent.
- **Operator independence**: The pilot is run by the same operator who
  authored the code. Independent replication is needed for stronger claims.