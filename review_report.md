# Methodological Review: reasoning-core Data Science & Statistical Rigor

**Reviewer**: Critical Data Science Reviewer (PhD Experimental Design & Statistical Methodology)
**Scope**: 6 files across eval harness, pre-registration, core scoring, and gate dispatch
**Total Findings**: 16 (3 CRITICAL, 6 HIGH, 5 MEDIUM, 2 LOW)

---

## CRITICAL FINDINGS

### C1. Pre-Registered Null Hypothesis is Untestable from Collected Data
```
SEVERITY: CRITICAL
FILE: eval/run_ablation.py:497-510
ISSUE: The pre-registration specifies H0: scorer_only(100) - plan_grounding_only(010) <= 5pp,
       but the code NEVER computes the direct paired comparison between these two arms.
       It only computes each non-vanilla arm against vanilla (000). The cross-arm contrast
       required to test the pre-registered hypothesis is absent.
EVIDENCE:
  # Lines 497-510: bootstrap is arm-vs-vanilla only
  for arm in arm_codes:
      if arm == "000":
          bootstrap_results[arm] = (0.0, 0.0, 0.0)
          continue
      arm_savings = [_token_savings(t, v) for t, v in zip(per_arm_tokens[arm], vanilla_tokens)]
      zero_baseline = [0.0] * len(arm_savings)
      mean_d, ci_lo, ci_hi = _paired_bootstrap_cis(arm_savings, zero_baseline, ...)
  # No code computes: savings_100[task_i] - savings_010[task_i]
FIX: Add a dedicated bootstrap comparison between arms "100" and "010" that computes
     paired differences per-task and reports mean + 95% CI. Also compute the
     one-sided p-value for H0: delta <= 5pp.
```

### C2. Pre-Registration Document is Completely Unenforced
```
SEVERITY: CRITICAL
FILE: eval/run_ablation.py (entire file)
ISSUE: The pre-registration at eval/preregistration/2026-05-13.md is never referenced,
       checked, or enforced by the code. There is no timestamp validation, hash
       verification, git-commit check, or parameter-matching assertion. An operator
       could modify the pre-registration after running arms, or run with parameters
       that contradict the pre-registration, with no enforcement mechanism.
EVIDENCE: grep -r "preregistration" eval/run_ablation.py returns nothing.
  No reference to pre-registration file, date validation, or parameter lock.
FIX: Add pre-registration validation: (a) verify the pre-registration file exists
     before any arm runs, (b) optionally verify a cryptographic hash of the file
     matches a committed value, (c) validate that --arms, --n, and other parameters
     match the pre-registered design. Fail fast if mismatched.
```

### C3. Spearman rho File Path Resolution Will Never Find Results
```
SEVERITY: CRITICAL
FILE: src/s2_core.py:775-798
ISSUE: _load_consensus_spearman_rho() glob pattern is "embedder_compare_*.json"
       but compare_embedders.py writes results to a SUBDIRECTORY:
       eval/runs/embedder_compare_<ts>/results.json. The glob looks for flat files
       but the actual files are nested one level deep. The function always returns
       None, so consensus_spearman_rho is always None, causing the consensus gate
       to always take the "rho < 0.7" branch regardless of actual embedder agreement.
EVIDENCE:
  # s2_core.py:781
  pattern = str(runs_dir / "embedder_compare_*.json")
  files = sorted(glob.glob(pattern), reverse=True)
  # compare_embedders.py:355 (writes to subdirectory)
  out_dir = DEFAULT_OUT_BASE / run_id  # run_id = "embedder_compare_<ts>"
  with open(out_dir / "results.json", "w") as fh: ...
  # Actual path: eval/runs/embedder_compare_20260513T120000Z/results.json
  # Glob pattern: embedder_compare_*.json  <- NEVER MATCHES
FIX: Change glob to find subdirectories then read results.json within:
     pattern = str(runs_dir / "embedder_compare_" / "*.json")
     -> Actually: glob_dirs = sorted(runs_dir.glob("embedder_compare_*"), reverse=True)
     for d in glob_dirs:
         results_file = d / "results.json"
         if results_file.exists(): ...
```

---

## HIGH FINDINGS

### H1. Pairwise Cohen's d Matrix is Computed from Synthetic Data, Not Real Outputs
```
SEVERITY: HIGH
FILE: eval/compare_embedders.py:297-322
ISSUE: The pairwise Cohen's d matrix and its bootstrap CIs are computed from
       synthetically generated Gaussian data (rng.gauss(...)), not actual embedder
       outputs. In both dry-run AND live modes, the code generates fake group data
       from summary statistics (raw_l2_mean, raw_l2_std) and bootstraps Cohen's d
       from these synthetic distributions. The resulting CIs have no grounding in
       actual embedder performance.
EVIDENCE:
  rng = random.Random(args.seed + i * 100 + j)
  group_i = [rng.gauss(results[arm_i].get("raw_l2_mean", 0.3),
                        results[arm_i].get("raw_l2_std", 0.1)) for _ in range(n)]
  group_j = [rng.gauss(results[arm_j].get("raw_l2_mean", 0.3),
                        results[arm_j].get("raw_l2_std", 0.1)) for _ in range(n)]
  d = _cohens_d(group_i, group_j)
  # ... bootstrap CI also from these synthetic groups
FIX: In live mode, collect the actual per-sample L2 distances from
     validate_embedder.py and compute Cohen's d from those raw values.
     Only fall back to synthetic data when RC_LIVE != 1 and document it.
```

### H2. Per-Embedder Z-Rescale is Computed but Never Applied to Pairwise Matrix
```
SEVERITY: HIGH
FILE: eval/compare_embedders.py:286-294
ISSUE: The per-embedder z-rescale computes cohens_d_rescaled = cohens_d / sigma_sep,
       but this rescaled value is stored in results[arm]["cohens_d_rescaled"] and
       NEVER used in the pairwise Cohen's d matrix computation (lines 297-322).
       The matrix uses the raw (unrescaled) cohens_d values. The rescale strategy
       parameter has no effect on the reported pairwise comparisons.
EVIDENCE:
  # Lines 286-294: rescale computed but not returned/used
  if args.rescale == "per-embedder":
      for arm in arms:
          r = results[arm]
          sigma = r.get("sigma_sep", 1.0)
          r["cohens_d_rescaled"] = r.get("cohens_d", 0.0) / sigma if sigma > 0 else ...
  # Lines 297-322: pairwise matrix uses raw values, not rescaled
FIX: Either (a) use cohens_d_rescaled in the pairwise matrix when rescale=="per-embedder",
     or (b) remove the unused computation and document that pairwise Cohen's d
     is always computed on raw scales. Currently it's dead code.
```

### H3. rho >= 0.7 Threshold is Arbitrary and Not Pre-Registered
```
SEVERITY: HIGH
FILE: src/hooks/_dispatch.py:541
ISSUE: The 0.7 threshold for Spearman rho in gate_consensus() is hardcoded with
       no theoretical or empirical justification. It is not mentioned in the pre-
       registration document (2026-05-13.md). The threshold determines which honesty
       framing is used when embedders disagree, but there's no calibration data,
       no power analysis, and no citation supporting 0.7 vs 0.6 or 0.8.
EVIDENCE:
  if spearman_rho is not None and spearman_rho >= 0.7:
      signal_source = "secondary_score_disagree"
  else:
      signal_source = "consensus_disagree"
  # Pre-registration (2026-05-13.md) has NO mention of consensus, rho, or 0.7.
FIX: (a) Add the threshold justification to the pre-registration with empirical
     calibration data, OR (b) derive the threshold from a held-out validation set
     where the relationship between rho and actual agreement rate is measured.
     Document the derivation.
```

### H4. Factorial Assumption Violated by Structural Gate Interactions
```
SEVERITY: HIGH
FILE: eval/run_ablation.py (design-level) + src/hooks/_dispatch.py
ISSUE: The 3-factor factorial assumes additive main effects, but the gates have
       structural interactions: RC_RULE_ENGINE only blocks when RC_S2_GATE=1
       (because the rule engine gate fires AFTER regression detection, which
       requires the scorer). Similarly, plan_grounding effects may be conditioned
       on scorer output. This creates a natural interaction that the main-effects
       LOO analysis cannot capture. The leave-one-out deltas will be biased
       estimates of marginal contributions.
EVIDENCE:
  # gate_regression fires first (requires scorer)
  # gate_rule_engine only evaluates if RC_RULE_ENGINE == "1" but its blocks
  # depend on report content which comes from the scorer
  # _leave_one_out_deltas averages across all arms, ignoring interaction structure
FIX: (a) Add explicit interaction term estimation to the LOO analysis,
     OR (b) acknowledge in the pre-registration that main effects are estimated
     under the assumption of no interactions and add a post-hoc interaction test.
     Report both main effects and interaction CIs.
```

### H5. LOO Analysis Uses Arm-Level Means Instead of Paired Task-Level Differences
```
SEVERITY: HIGH
FILE: eval/run_ablation.py:263-274
ISSUE: The leave-one-out delta computation collapses each arm's data to a single
       mean (statistics.mean of token savings) before computing contrasts. This
       discards the paired structure — the same tasks run across all arms. The
       proper approach is to compute paired differences at the task level (savings
       for task i in arm A minus savings for task i in flipped arm A'), then
       average those paired differences. The current approach inflates variance
       by treating arm means as independent rather than paired.
EVIDENCE:
  arm_savings = statistics.mean(
      _token_savings(t, v) for t, v in zip(per_arm_tokens[arm], vanilla_tokens)
  )
  flipped_savings = statistics.mean(
      _token_savings(t, v) for t, v in zip(per_arm_tokens[flipped_str], vanilla_tokens)
  )
  deltas.append(flipped_savings - arm_savings)
  # ^ Uses arm-level means, not task-level paired differences
FIX: Compute paired differences per-task:
     for t_idx in range(n_tasks):
         s_arm = _token_savings(per_arm_tokens[arm][t_idx], vanilla_tokens[t_idx])
         s_flip = _token_savings(per_arm_tokens[flipped][t_idx], vanilla_tokens[t_idx])
         task_deltas.append(s_flip - s_arm)
     deltas.append(statistics.mean(task_deltas))
```

### H6. Spearman Stdlib Fallback Does Not Handle Ties (Produces Incorrect rho)
```
SEVERITY: HIGH
FILE: eval/compare_embedders.py:117-122
ISSUE: The stdlib fallback for Spearman rho uses the formula 1 - 6*d2/(n(n^2-1)),
       which is ONLY valid when there are no tied ranks. With ties (common in
       real AIS score data where multiple edits get similar scores), this formula
       produces incorrect results. The scipy path handles ties correctly, but if
       scipy is unavailable, the fallback silently gives wrong answers.
EVIDENCE:
  # Stdlib fallback (lines 117-122):
  rank_x = sorted(range(n), key=lambda i: x[i])
  rank_y = sorted(range(n), key=lambda i: y[i])
  d2 = sum((rank_x.index(i) - rank_y.index(i)) ** 2 for i in range(n))
  return 1.0 - (6.0 * d2) / (n * (n * n - 1))  # WRONG with ties
  # Note: O(n^2) due to .index() inside sum — also inefficient
FIX: (a) Add a tie-aware stdlib fallback using average ranks, OR (b) make scipy
     a hard dependency for compare_embedders.py and raise an error if unavailable.
     Do not silently produce incorrect statistics.
```

---

## MEDIUM FINDINGS

### M1. Block Rates Are Hardcoded to Zero — Never Populated from Data
```
SEVERITY: MEDIUM
FILE: eval/run_ablation.py:516-518 + eval/aggregate.py
ISSUE: Decision block rates per gate are initialized to 0.0 for all gates and never
       populated from actual audit data. The report claims to show "Decision block
       rates per gate" but these are synthetic zeros. The comment says "populated
       from audit data in live runs" but no such population exists. Additionally,
       only the first 3 of 7 GATE_IDS are shown.
EVIDENCE:
  block_rates: dict[str, dict[str, float]] = {}
  for arm in arm_codes:
      block_rates[arm] = {g: 0.0 for g in GATE_IDS[:3]}  # Always zeros
  # No code reads audit events to populate actual block rates.
FIX: Remove the block rates section from the report, OR implement actual audit
     data ingestion to compute per-gate block rates from the audit JSONL files.
     If removed, update the pre-registration to remove "Secondary Metric:
     Decision-block rate per gate" since it's not actually measured.
```

### M2. Only First Breaching Dimension is Recorded — Loss of Diagnostic Information
```
SEVERITY: MEDIUM
FILE: src/s2_core.py:995-1001
ISSUE: The `break` in the dim_ceiling_breached loop means only the FIRST risk
       dimension exceeding the ceiling is recorded in fired_dims/fired_margins.
       If multiple dimensions simultaneously breach, only the lowest-index one is
       captured. This loses clinically relevant diagnostic information about
       multi-dimensional risk.
EVIDENCE:
  for i, rv_val in enumerate(risk_vector):
      if rv_val > dim_ceiling:
          fired_conditions.append("dim_ceiling_breached")
          fired_dims.append(RISK_LABELS[i])
          fired_margins[f"{RISK_LABELS[i]}_ceiling"] = float(rv_val - dim_ceiling)
          break  # <-- only records first breach
FIX: Remove the break to record ALL breaching dimensions:
  for i, rv_val in enumerate(risk_vector):
      if rv_val > dim_ceiling:
          fired_conditions.append("dim_ceiling_breached")
          if i < len(RISK_LABELS):
              fired_dims.append(RISK_LABELS[i])
              fired_margins[f"{RISK_LABELS[i]}_ceiling"] = float(rv_val - dim_ceiling)
  # No break — collect all breaches
```

### M3. Fired Conditions Attribution is Descriptive, Not Causal
```
SEVERITY: MEDIUM
FILE: src/s2_core.py:982-1003
ISSUE: The fired_conditions mechanism lists which thresholds were crossed, but
       this is descriptive attribution, not causal explanation. When both
       "ais_below_threshold" and "dim_ceiling_breached" fire, they may be
       correlated symptoms of the same underlying code change (e.g., a large
       refactor simultaneously reduces embedding similarity AND increases
       cyclomatic complexity). Listing both does not establish which caused
       the regression flag — there's no counterfactual reasoning (what would
       have happened if only one fired?).
EVIDENCE:
  regression = len(fired_conditions) > 0  # Simple OR of thresholds
  # No causal model, no counterfactual, no do-calculus, no Shapley values.
  # The comment says "Honest attribution (Point 5 — replaces pseudo-SHAP)"
  # but the replacement is just threshold-crossing flags, not actual attribution.
FIX: Document explicitly that fired_conditions are "threshold-crossing indicators"
     not "causal attributions." If causal interpretation is needed, implement
     Shapley-value attribution over the risk vector dims relative to the
     regression decision boundary.
```

### M4. Spearman rho Computed from Synthetic Data with Out-of-Range Values
```
SEVERITY: MEDIUM
FILE: eval/compare_embedders.py:329-333
ISSUE: In dry-run mode, Spearman rho between primary and bge-code embedders is
       computed from synthetic data: primary_ais = Uniform[0,1], bge_ais = primary + N(0,0.1).
       This can produce bge_ais values outside [0,1] (the valid AIS range), and
       creates artificially high correlation (rho typically >0.95) because noise
       is small relative to the uniform signal. The dry-run behavior will produce
       unrealistically high pre-shipped rho values.
EVIDENCE:
  primary_ais = [rng.random() for _ in range(n)]  # Uniform[0,1]
  bge_ais = [p + rng.gauss(0, 0.1) for p in primary_ais]  # May exceed [0,1]
  spearman_rho = _spearman_rho(primary_ais, bge_ais)
  # Expected rho ~0.95+ (signal-to-noise ratio ~ sqrt(1/12) / 0.1 ~ 2.9)
FIX: Clip bge_ais to [0,1] and add realistic noise (e.g., N(0, 0.3)) to match
     expected real-world embedder disagreement. Better: only pre-ship rho from
     actual live runs, never from synthetic data.
```

### M5. Potential Model Service State Confounding Across Arms
```
SEVERITY: MEDIUM
FILE: eval/run_ablation.py:173-206
ISSUE: Each (task, arm) pair runs as a separate subprocess, but if the SSM
       backbone /score service is persistent across invocations (a shared HTTP
       service on port 8765), model-level caching or state could confound arm
       comparisons. Earlier arms may warm caches that benefit later arms for the
       same task. The ablation script has no mechanism to clear service state
       between arms.
EVIDENCE:
  # _run_one invokes run_task.sh per (task, arm) pair
  proc = subprocess.run(cmd, timeout=timeout_s + 60, ...)
  # No service restart, no cache clearing, no state isolation between arms.
FIX: Document the assumption that the scorer service is stateless, OR add a
     /health or /clear_cache endpoint call between arms, OR restart the service
     between arm groups. At minimum, randomize the (task, arm) schedule more
     aggressively to distribute cache-warmup effects evenly (already partially
     done via per-task arm shuffling).
```

---

## LOW FINDINGS

### L1. Spearman vs Pearson Choice Not Pre-Registered
```
SEVERITY: LOW
FILE: src/hooks/_dispatch.py:522-557 + eval/compare_embedders.py:107-122
ISSUE: Spearman rank correlation is used for comparing embedder AIS scores, but
       the choice is not pre-registered. Pearson would be more aligned with the
       absolute-difference disagreement threshold (|ais1 - ais2| > 0.2) since
       Pearson measures linear agreement. Spearman is defensible for monotonic
       robustness but the justification is absent. Both metrics are on the same
       [0,1] scale and the relationship is expected to be approximately linear.
EVIDENCE: Pre-registration (2026-05-13.md) has no mention of consensus,
          embedder comparison, or correlation metric choice.
FIX: Add metric justification to pre-registration: "Spearman chosen over Pearson
     because we care about rank agreement, not absolute scale; robust to outliers
     and non-linear distortions." Alternatively, report both and pre-specify
     which one gates the decision.
```

### L2. Consensus Gate Honesty Framing is Semantically Confusing
```
SEVERITY: LOW
FILE: src/hooks/_dispatch.py:541-544
ISSUE: The signal_source naming is paradoxical: when rho < 0.7 (low agreement),
       the signal is "consensus_disagree" — but if embedders don't agree, there
       is no consensus to violate. When rho >= 0.7 (high agreement), the signal
       is "secondary_score_disagree" — which is the more honest framing. While
       the intent (avoid misleading "consensus" language when models don't
       actually agree) is laudable, the resulting naming is semantically inverted
       and will confuse operators reading the audit logs.
EVIDENCE:
  if spearman_rho is not None and spearman_rho >= 0.7:
      signal_source = "secondary_score_disagree"
  else:
      signal_source = "consensus_disagree"  # Paradox: no consensus exists
FIX: Rename to explicitly reflect the intent:
  if spearman_rho is not None and spearman_rho >= 0.7:
      signal_source = "embedders_usually_agree_but_disagree_now"
  else:
      signal_source = "embedders_poorly_correlated_disagreement_expected"
```

---

## VERIFIED CORRECT (No Issues Found)

| Component | Status | Evidence |
|-----------|--------|----------|
| Paired bootstrap resamples differences (not raw values) | CORRECT | `diffs = [v - b for v, b in zip(values, baseline_values)]` (run_ablation.py:140) |
| Percentile method index computation | CORRECT | `lower_idx = int(n_resamples * alpha / 2)` with clamping (run_ablation.py:149-153) |
| n=50 with 10k resamples | ADEQUATE | Standard bootstrap theory; 10k >> 1k minimum |
| Cohen's d pooled SD formula | CORRECT | `sqrt(((na-1)*var_a + (nb-1)*var_b) / (na+nb-2))` (compare_embedders.py:101) |
| fired_margins sign correctness | CORRECT | All margins positive when threshold exceeded |
| Baseline (vanilla=000) definition | CORRECT | Enforced by code; auto-added if missing |
| Wilcoxon zero_method consistency | CORRECT | Both scipy and stdlib paths drop zero differences |
| BCa bootstrap implementation | CORRECT | Bias correction + jackknife acceleration properly implemented |
| Holm-Bonferroni across metrics | ACCEPTABLE | Conservative but valid for correlated tests |

---

## SUMMARY TABLE

### Findings by Severity

| Severity | Count | Categories |
|----------|-------|------------|
| CRITICAL | 3 | Pre-registration (2), File I/O (1) |
| HIGH | 6 | Synthetic data (2), Statistical method (2), Threshold arbitrariness (1), Factorial design (1) |
| MEDIUM | 5 | Missing data (1), Information loss (1), Causal attribution (1), Synthetic data (1), Confounding (1) |
| LOW | 2 | Metric choice (1), Naming confusion (1) |
| **Total** | **16** | |

### Findings by Category

| Category | CRITICAL | HIGH | MEDIUM | LOW | Total |
|----------|----------|------|--------|-----|-------|
| Pre-registration / prereg fidelity | 2 | 1 | 0 | 0 | 3 |
| Bootstrap / statistical inference | 0 | 2 | 0 | 0 | 2 |
| Cohen's d / embedder comparison | 0 | 2 | 1 | 1 | 4 |
| Consensus gate / Spearman rho | 1 | 1 | 0 | 1 | 3 |
| Fired conditions attribution | 0 | 0 | 2 | 0 | 2 |
| Factorial ablation design | 0 | 1 | 0 | 0 | 1 |
| Data quality / synthetic data | 0 | 0 | 2 | 0 | 2 |

### Blocking Issues for Publication

The following MUST be resolved before results can be considered scientifically valid:

1. **C1**: The pre-registered hypothesis cannot be tested from current data — add cross-arm comparison
2. **C2**: Pre-registration has no enforcement — add validation
3. **C3**: Spearman rho loader is broken — fix glob pattern
4. **H1**: Pairwise Cohen's d uses synthetic data — use real embedder outputs
5. **H4**: Factorial interactions are ignored — acknowledge or estimate
6. **H5**: LOO uses unpaired arm means — use paired task-level differences
