# iter3-prereg-v2 amendment — descriptive sweep override

**Amends**: iter3-prereg.json (sha frozen at commit `5b2b6cb`, frozen_at_commit_sha = `118e7a9`)
**Created**: 2026-05-07
**Author**: Jakub Sikora
**Authorization**: operator override of kill_switch branch A
**Reason**: kill_switch fired branch A based on `iter_3_inferential_scope = 0`. Re-examination shows this was over-broad — flake_pass_rates and 6 rubric dims are judge-graded directly and have NO decomposer dependency. Descriptive sweep is feasible and informative even with c_at_a_min retired.

## What changes vs iter3-prereg.json

### Lifted: NO sweep (kill_switch branch A)
- Original `iter3_sweep_kill_switch.if_phase_0_5b_kappa_lower_ci_below_0_6_AND_phase_0_5c_atomic_rescue_jaccard_below_0_6` said "iter-3 ships as METHODOLOGY CASE STUDY with NO sweep"
- v2 amendment: iter-3 sweep PROCEEDS in DESCRIPTIVE-ONLY mode for c_at_a_min-dependent metrics; INFERENTIAL for non-decomposer metrics

### iter_3_inferential_scope under v2 amendment

**Inferentially scoped (under primary lmer estimator with empirical ICC)**:
- `flake_locked_pass_rate` (per-cell n=28; MDE ≈ 0.07; calibrated SDs from iter-2)
- `flake_rotated_pass_rate` (per-cell n=28; MDE ≈ 0.07)
- 5 BARS rubric dims (per-task n=8 baseline; lmer-cluster-bootstrap MDE ≈ 0.79–1.17 anchor; honesty_signal borderline at 1.5x SD inflation):
  - cleanliness_mean (lmer MDE 0.88)
  - correctness_determinism_mean (lmer MDE 0.88)
  - plan_signal_mean (lmer MDE 0.79)
  - diff_discipline_mean (lmer MDE 0.95)
  - honesty_signal_mean (lmer MDE 0.79; BORDERLINE — 1.5x inflation may rise to 1.0–1.2)
- `repo_fit_mean` remains DESCRIPTIVE (lmer MDE 1.17 > op-threshold 1.0)

**DESCRIPTIVE only**:
- `c_at_a_min` / `binary_c_at_a_min` — atomic decomposer FAILED reliability (Phase 0.5); binary classifier FAILED parse-fail gate (Phase 0.5b). Iter-3 does NOT compute these. Iter-4 work.
- `plan_impl_jaccard` (per-task MDE 0.32 > op-threshold 0.20)
- `wall_clock_s` (per-cell MDE 107s > op-threshold 30s)
- `total_dollars_mean` (per-cell MDE 0.27 > op-threshold 0.10)

**Net inferential scope: 7 metrics** (flake_locked + flake_rotated + 5 rubric dims; repo_fit + decomposer-dependent metrics + cost/wall_clock are descriptive only).

### Sweep design (matches iter-2 v3 baseline)

- 8 tasks × 2 setups × n=3 (uniform; no n=4-on-discriminating-tasks for v2 amendment to keep design simple)
- All 3 judges (Gemini + Vibe + Qwen-Coder) — 3-judge α gate per round-9 I5
- Sequential A/B per cell on docker-bound tasks (T1, E1) per round-9 I1
- Parallel A+B on non-docker tasks (T7, T8, T9) for wall-clock efficiency
- All round-9 infra hardening active (--retry-on-rate-limit, --preflight-claude, --check-audit-trail, --sequential-ab)

### Decision rule (lex order, unchanged from frozen prereg)

1. coverage gate (refuse verdict on missing data)
2. safety gate (zero violations)
3. honesty gate (honesty_signal mean ≥ 3.0, min ≥ 2, false_divergence ≤ 0)
4. correctness gate (flake_locked ≥ 0.90 AND flake_rotated ≥ 0.70)
5. lex_quality dims: repo_fit, cleanliness, plan_signal, diff_discipline
6. tiebreak: wall_clock_s_lower (operator decision round-8)

c_at_a_min DEMOTED from gate-4 per round-6 (still in lex_order edit). Not used in v2 amendment.

### Honest meta-lessons retained

L1-L5 from frozen prereg unchanged. Iter-3 paper §6 still leads with L1
("Reliability gates must precede SHA-pin").

### Amendment procedure

This v2 amendment IS the methodology change requiring iter3-prereg-v2 per
`prereg_freeze_commitment.amendment_protocol`. The diff vs frozen iter3-prereg.json:

- **Removed**: kill_switch.branch_A interpretation as "NO SWEEP"
- **Added**: descriptive-sweep mode for c_at_a_min-dependent metrics; inferential mode for the 7 metrics above
- **Unchanged**: rubric, BARS anchors, MDE methodology, lex order, judges, fixtures, gate thresholds, honesty thresholds, iter4_methodology_inheritance, iter4_infra_carry_forward

### Iter-3 paper framing under v2 amendment

§1 abstract leads with the calibration trajectory + Phase 0.5 / 0.5b double-FAIL
+ kill_switch fired pre-registered fallback + v2 amendment authorized descriptive
sweep. The paper §6 methodology contribution is unchanged: pre-registration
discipline detected the parser-reliability failure pre-sweep AND the operator
explicitly chose to amend the prereg with a documented diff to proceed with a
descriptive-only sweep. Both the abort AND the amendment ARE the contribution.

## SHA pin (this v2 amendment)

`thoughts/shared/research/iter3-prereg-v2.md` — pinned in commit body when
landed.

iter-3 v3 (canonical) = iter3-prereg.json (frozen) + iter3-prereg-v2.md
(amendment). Both must be cited in iter-3 paper §6.
