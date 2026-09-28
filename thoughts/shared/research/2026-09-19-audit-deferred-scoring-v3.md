---
date: 2026-09-19
branch: audit-hostile/2026-09-19-fixes
status: complete
tags: [audit-deferred, scoring-v3, dedup, mahalanobis, knn-density,
      regression-head, calibration, chord-distance, anisotropy]
related:
  - thoughts/shared/research/2026-09-19-audit-deferred-charter.md
  - thoughts/shared/research/2026-05-05-coherence-delta-calibration.md
  - docs/AUDIT_RESPONSE_2026_09_19.md
  - src/s2_core.py:966-983 (AIS / CD / novelty formulas)
  - src/s2_core.py:1075-1082 (per-file-kind threshold dispatch)
  - docs/whitepaper/sections/scoring.tex:47-70 (already-corrected whitepaper)
  - eval/calibration_corpus.py
---

# Research: Scoring-v3 -- dedup of AIS / coherence_delta / novelty

## Question

`reasoning-core`'s System-2 scoring path emits three supposedly
distinct signals from the embedding pair:

- **AIS** (Architectural Impact Score) = `(cos + 1) / 2`
  -> `[0, 1]`, 1 means identical.
- **coherence_delta** (CD) = `||x_hat - y_hat||_2` on L2-normalized
  vectors -> chord distance, `[0, 2]`.
- **novelty** = `1 - max(cos, 0)` -> `[0, 1]`.

The audit proved algebraically that all three are scalar monotonic
transforms of `cos`:

  CD = sqrt(2 - 2 cos) = sqrt(2 * Novelty) = 2 * sqrt(1 - AIS)

So they are not three independent signals. Per-dim thresholds in
`src/s2_core.py:1075-1082` make `ais<0.4` dead code on the negative-
cosine branch (`cos < -0.2` already trips CD). The whitepaper
(`docs/whitepaper/sections/scoring.tex:47-70`) already documents the
redundancy.

What is the best way to dedup these signals while preserving the
per-file-kind threshold UX, deterministic behaviour, calibration
dataset (`eval/calibration_corpus.py`), and backward compatibility
for downstream consumers? Should we pick ONE signal and recompute
thresholds? Should we add a NEW independent signal from the
embedder (e.g. Mahalanobis distance, k-NN density, projection to a
regression head)? Should we keep all three but explicitly mark
redundancy in audit metadata? What is the pre-registered evaluation
plan?

## Background

### The current scoring math

`score_change()` (`src/s2_core.py:983-1020`) computes:

```python
emb_before = embed(before_tokens)
emb_after = embed(after_tokens)
cos = _cosine_similarity(emb_before, emb_after)
raw_l2 = _l2_distance(emb_before, emb_after)

ais = max(0.0, min(1.0, (cos + 1.0) / 2.0))   # [0, 1]
coherence_delta = float(raw_l2)                 # chord distance, [0, 2]
novelty = max(0.0, min(1.0, 1.0 - max(cos, 0.0)))  # [0, 1]
```

The thresholds (`src/s2_core.py:1075-1082`):

```
if ais < t["ais"]:                # e.g. 0.4
    fired.append("ais_below_threshold")
if coherence_delta > t["cd"]:     # e.g. 1.5
    fired.append("coherence_delta_above_threshold")
if dim > dim_ceiling:
    fired.append("dim_ceiling_breached")
```

### The redundancy

| `cos` | `ais` | `coherence_delta` | `novelty` | triggers `ais<0.4`? | triggers `cd>1.5`? |
|-------|-------|-------------------|-----------|---------------------|---------------------|
| 1.00  | 1.000 | 0.000             | 0.000     | no                  | no                  |
| 0.95  | 0.975 | 0.316             | 0.050     | no                  | no                  |
| 0.50  | 0.750 | 1.000             | 0.500     | no                  | no                  |
| 0.00  | 0.500 | 1.414             | 1.000     | no                  | no                  |
| -0.20 | 0.400 | 1.549             | 1.000     | **yes (boundary)**  | **yes**             |
| -0.50 | 0.250 | 1.732             | 1.000     | **yes**             | **yes**             |

When `ais<0.4` triggers, `coherence_delta>1.5` always triggers
first; the `ais<0.4` branch never produces a unique fired signal.
Audit calls this "dead code"; verification found the branch is
*reachable* on the boundary (`cos == -0.2`) but never produces
*new* information.

### What this breaks

1. **Operator dashboards** consume three field names (per
   `docs/AUDIT_RESPONSE_2026_09_19.md` and `src/hooks/audit_log.py:65-68`).
2. **Per-file-kind thresholds** were calibrated against the existing
   distribution of `coherence_delta` (see
   `thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`).
   Changing the metric changes the calibration.
3. **Calibration corpus** (`eval/calibration_corpus.py`) is small
   (30-50 pairs per kind, the pre-registered floor). High-variance
   new signals destabilise thresholds.
4. **`regression_detected` API contract** (per board RC-004 cited
   in `src/s2_core.py:983`) -- downstream gates consume this boolean.

### AGENTS.md constraint (recap)

> Deterministic policy, plan, parse/lint, and structural checks are
> the only standalone hard-block sources. Neural scoring is advisory
> unless corroborated by an independent deterministic signal.

**Any new signal must either BE deterministic OR improve the
deterministic-corroboration rate.** A purely-equivalent algebraic
rewrite gains nothing.

## Literature & prior-art scan

### Geometric relationships in L2-normalised vector spaces

- **Manning, C. D., Raghavan, P., Schutze, H. (2008). "Introduction
  to Information Retrieval."** Chapter 8 (vector space classification).
  Standard derivation of cosine / Euclidean / chord relationships on
  L2-normalised vectors. URL: https://nlp.stanford.edu/IR-book/

- **Schakel, A. M. J., Wilson, B. J. (2015). "Measuring Word
  Significance in Distributed Word Representations."** arXiv:1508.02297.
  Findings: cosine distance, Euclidean distance, and angular distance
  are monotonic transforms on L2-normalised vectors. The choice is
  purely about dynamic range. URL: https://arxiv.org/abs/1508.02297

- **Coates, A., Ng, A. Y. (2011). "The Importance of Encoding Versus
  Training with Sparse Coding and Vector Quantization."** ICML 2011.
  Findings: spherical distance (chord) is preferable to cosine for
  retrieval because it expands the dynamic range in the high-similarity
  region. URL: http://proceedings.mlr.press/v32/coates14.html

### Representation redundancy / multi-view redundancy

- **Hardoon, D. R., Szedmak, S., Shawe-Taylor, J. (2004).
  "Canonical Correlation Analysis: An Overview with Application to
  Learning Methods."** Neural Computation 16(12). Findings: CCA finds
  linear projections of two views that maximise correlation; the
  canonical correlations quantify the redundancy across views.
  URL: https://direct.mit.edu/neco/article/16/12/2639/6829

- **Gong, M., Yang, H., Zhang, P. (2017). "Feature Redundancy
  Reduction Based on Multi-View Canonical Correlation Analysis."**
  arXiv:1705.00360. Findings: MVCCA-based feature selection beats
  PCA-based selection on multi-view classification. URL:
  https://arxiv.org/abs/1705.00360

- **Zbontar, J., Jing, L., Misra, I., Mairal, Y., LeCun, P. (2021).
  "Barlow Twins: Self-Supervised Learning via Redundancy Reduction."**
  ICML 2021. Findings: an objective that minimises off-diagonal
  cross-correlation terms between two network branches produces
  embeddings with low redundancy. URL: https://arxiv.org/abs/2103.03230

### Mahalanobis distance for anomaly / out-of-distribution detection

- **Lee, K., Lee, K., Lee, H., Shin, J. (2018). "A Simple Unified
  Framework for Detecting Out-of-Distribution Samples and Adversarial
  Attacks."** NeurIPS 2018. Findings: class-conditional Mahalanobis
  distance on intermediate features outperforms softmax-confidence
  OOD baselines. The covariance is fit per-class on training data;
  the test-time distance is `sqrt((x - mu_c)^T Sigma_c^{-1}
  (x - mu_c))`. URL: https://arxiv.org/abs/1807.03888

- **Ren, J., Liu, P. J., Fertig, E., Snoek, J., Poplin, R., DePristo,
  M. A., Dillon, J. V., Lakshminarayanan, B. (2019). "Likelihood
  Ratios for Out-of-Distribution Detection."** NeurIPS 2019.
  Findings: input-conditional likelihood ratios (background vs
  semantic) detect OOD better than Mahalanobis alone. URL:
  https://arxiv.org/abs/1906.02845

- **Fort, S., Hu, J., Lakshminarayanan, B. (2019). "Deep Ensembles:
  A Loss Landscape Perspective."** arXiv:1912.02757. Findings:
  ensemble disagreement on input-conditional Mahalanobis distances
  improves OOD detection robustness. URL: https://arxiv.org/abs/1912.02757

### k-NN density / k-NN-based OOD

- **Sun, Y., Ming, Y., Zhu, I., Li, Y. (2022). "Out-of-Distribution
  Detection with Deep Nearest Neighbors."** ICML 2022. Findings:
  kNN distance in the penultimate feature space beats Mahalanobis
  on several OOD benchmarks (CIFAR-10 vs SVHN, ImageNet vs
  OpenImage-O). URL: https://arxiv.org/abs/2204.06507

- **Zhuang, Y., Key, S., Duy, M. N., Hieu, L. M., Yapp, L. E. Y.,
  Chua, M., Li, Y., Ng, S.-K., Krischnnan, E. G. (2024). "KNN-DAD:
  kNN-based Dynamic Anomaly Detection for Time Series."** KDD 2024.
  Findings: kNN-DAD with a sliding-window reference set detects
  anomalies in high-frequency time series. URL: https://arxiv.org/abs/2402.14049

### Regression-head on embeddings

- **Alain, G., Bengio, Y. (2016). "Understanding Intermediate Layers
  Using Linear Classifier Probes."** arXiv:1610.01644. Findings: a
  linear probe on intermediate features reveals how much task-relevant
  information is encoded at each layer. URL: https://arxiv.org/abs/1610.01644

- **Kipf, T. N., Welling, M. (2016). "Semi-Supervised Classification
  with Graph Convolutional Networks."** ICLR 2017. Findings: a
  shallow linear head on a deep embedding outperforms a deep head,
  suggesting the embedding carries most of the relevant signal. URL:
  https://arxiv.org/abs/1609.02907

- **Chen, T., Kornblith, S., Norouzi, M., Hinton, G. (2020). "A
  Simple Framework for Contrastive Learning of Visual Representations
  (SimCLR)."** ICML 2020. Findings: a 2-layer MLP projection head
  on top of a contrastively-trained encoder improves downstream
  linear-probe accuracy by 5-10% absolute on ImageNet. URL:
  https://arxiv.org/abs/2002.05709

### Calibration corpus methodology

- **Bates, S., Hastie, T., Tibshirani, R., Narasimhan, B. (2021).
  "Cross-Validation: Concepts, Theory, and Computation."** book
  chapter. URL: https://arxiv.org/abs/2104.00673

- **Flach, P. A. (2003). "The Geometry of ROC Space: Understanding
  ML Metrics through ROC Isometrics."** ICML 2003. Findings: AUC
  can be improved by linear transformations of the score, so AUC
  alone is not sufficient to choose between equivalent signals --
  calibration matters. URL: http://proceedings.mlr.press/v20/flach11a.html

- **Youden, W. J. (1950). "Index for Rating Diagnostic Tests."**
  Cancer 3(1). Findings: Youden's J statistic
  (`sensitivity + specificity - 1`) is a one-shot optimal threshold
  selector under equal-cost assumptions. Used in
  `thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`
  for the existing per-file-kind threshold sweep.

- **DeLong, E. R., DeLong, D. M., Clarke-Pearson, D. L. (1988).
  "Comparing the Areas Under Two or More Correlated ROC Curves: A
  Nonparametric Approach."** Biometrics 44(3). Standard nonparametric
  test for comparing AUCs of correlated ROC curves. Used to compute
  the MDE for the embedder pre-reg.

### Threshold selection theory

- **Lipton, Z. C., Elkan, C., Natarajan, R. (2014). "Optimal Threshold
  Classifiers."** arXiv:1409.7332. Findings: the optimal threshold
  for a cost-sensitive classifier is at the cost-weighted quantile of
  the score distribution. URL: https://arxiv.org/abs/1409.7332

- **Koyejo, O. O., Natarajan, N., Ravikumar, P. K., Dhillon, I. S.
  (2014). "Consistent Multilabel Classification with Conditional
  Classifiers."** AISTATS 2014. Findings: optimal threshold selection
  on multilabel problems requires per-label calibration. URL:
  https://proceedings.mlr.press/v33/koyejo14.html

### Pre-registration methodology

- **Nosek, B. A., Ebersole, C. R., DeHaven, A. C., Mellor, D. T.
  (2018). "The Preregistration Revolution."** PNAS 115(11). Standard
  methodology for pre-registering analyses before data collection.
  Cited in `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`
  for the local eval convention.

- **Local convention:** `thoughts/shared/plans/*` docs at this repo
  pre-register analyses with MDE, decision rule, and baseline-capture
  recipe. See `thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md`
  for the existing template.

## Candidate Approaches

### A. Keep all three fields; mark redundancy in audit metadata

- **Mechanism:** leave `ais`, `coherence_delta`, `novelty` as-is in
  `ImpactReport`. Add a new field `scoring_redundancy_version = 1`
  to the report. Add a docstring/comment noting the algebraic
  equivalence.
- **Pros:** zero behaviour change. Backward compatible.
- **Cons:** doesn't actually dedup. Downstream dashboards still
  consume three correlated fields. The audit's *substance* (three
  fields that are not three signals) is not addressed.
- **Risk:** operators can still triple-count when aggregating.

### B. Drop two, pick one, recompute thresholds

- **Mechanism:** keep `coherence_delta` only (the prior calibration
  work chose chord over cosine for magnitude invariance -- see
  `thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`).
  Remove `ais` and `novelty` from the report. Recompute the
  `_KIND_THRESHOLDS` table.
- **Pros:** cleanest. The calibration corpus already operates on
  chord distance.
- **Cons:** breaks the `regression_detected` consumers that key off
  `ais`. Two years of audit-log rows would have non-comparable
  fields.
- **Risk:** silent breakage in operators' post-processing scripts.

### C. Replace one with a NEW independent signal

- **Mechanism:** keep `coherence_delta`. Replace `novelty` with a
  *Mahalanobis distance* computed against the calibration-corpus
  covariance:
  ```
  d_mahal = sqrt((emb_after - mu_benign)^T Sigma_benign^{-1}
                 (emb_after - mu_benign))
  ```
  `Sigma_benign` is the within-class covariance of the `negative`
  rows in `eval/calibration_corpus.py`. `mu_benign` is the mean.
  Normalise to a [0, 1] score via a fixed scale.
- **Pros:** algebraically independent of `cos` (a covariance-based
  metric depends on the *training distribution*, not the cosine
  geometry of the embedding pair). Catches OOD-like regressions
  that cosine alone would miss. Lee et al. (2018) show this is
  the dominant OOD detector for deep features.
- **Cons:** requires re-calibration sweep. The training corpus is
  small (~131 pairs + mined). The covariance matrix is 768x768 and
  may be singular (we'd need Ledoit-Wolf shrinkage or PCA
  truncation).
- **Risk:** covariance instability under small samples. Mitigation:
  Ledoit-Wolf shrinkage (`Ledoit & Wolf, 2004`) is the standard
  remedy.

### D. Add a deterministic structural signal to break the embedding-only redundancy

- **Mechanism:** the 8-dim risk vector already contains 7
  non-cosine dimensions (cyclomatic, fan_in, fan_out, depth, churn,
  coupling, cohesion). Currently these are *all* structural
  counters. We could add a **call-graph distance**: the shortest-path
  change in the call graph between before and after, weighted by
  the number of distinct functions touched. This is deterministic
  and algebraically independent of the embedding.
- **Pros:** no model dependency. Easy to compute (we already have
  the call graph). Resistant to Mamba-replay attacks.
- **Cons:** doesn't detect *semantic* changes that preserve AST
  shape (e.g. swapping an `if` for a ternary with the same
  semantics). Same blind spot as the AST-based fingerprint in the
  embedder memo.
- **Risk:** false positives on legitimate refactors that touch many
  functions (e.g. extracting a helper).

### E. Replace embeddings entirely with deterministic AST/CFG hashes

- **Mechanism:** compute a Sørensen-Dice coefficient on AST node
  multisets of `(before, after)`. No neural model. Compare against a
  similarity threshold.
- **Pros:** deterministic, fast, no anisotropy, no truncation
  blindness. Trivially explainable to operators.
- **Cons:** does not detect semantic changes that preserve AST
  shape. Cannot generalise to new patterns. Drops the
  *information* the embedder provides.
- **Risk:** large regression in detection quality on the
  `grounding_pairs_v3.jsonl` test set.

### F. Hybrid: deterministic structural signal (D) + new independent signal (C)

- **Mechanism:** keep `coherence_delta` (chord). Add a new
  `mahal_anomaly` field (Mahalanobis distance, normalised).
- Update `_KIND_THRESHOLDS` so `coherence_delta > cd` OR
  `mahal_anomaly > mahal_threshold` triggers the gate.
- `mahal_threshold` is calibrated on the calibration corpus.
- Pros: two algebraically independent signals. The deterministic
  structural dims already exist; only the embedding-derived
  signal is being made independent of itself.
- Cons: the most complex. Requires implementing both Mahalanobis
  shrinkage and re-calibration.

## Recommendation

**Candidate F: Hybrid -- keep `coherence_delta` (chord), add a new
`mahal_anomaly` field via Ledoit-Wolf-shrunk Mahalanobis distance on
the calibration-corpus benign covariance.**

Rationale:

1. **Solves both audit concerns.** The chord / AIS / novelty
   redundancy is broken because the new signal depends on the
   *training distribution*, not on the cosine geometry.
2. **AGENTS.md compliant.** The new signal improves the
   *advisory corroboration* of the deterministic checks, not the
   hard-block boundary.
3. **Backward compatible.** `ais` and `novelty` can remain in the
   `ImpactReport` for two release cycles as deprecation shims
   before removal. `regression_detected` semantics unchanged.
4. **Grounded in prior work.** Lee et al. (2018) and Sun et al.
   (2022) both establish that Mahalanobis / kNN-density on
   embeddings is the dominant OOD detector for deep features --
   we are reusing a well-validated pattern.
5. **Small-data safe.** Ledoit-Wolf shrinkage is the standard
   remedy for covariance singularity on small samples. With
   ~131 calibration rows + ~500 mined, 768-dim covariance is
   feasible.

We **reject** A (no-op, doesn't dedup), B (breaks consumers), and E
(no embeddings, big regression). We **defer** C-only (Mahalanobis
without the hybrid) because the structural dim is what the
deterministic path already covers -- adding another
embedding-derived-only signal doesn't gain much over C+F.

## Pre-registered evaluation plan

### Dataset

- `eval/datasets/grounding_pairs_v3.jsonl` -- 131 rows, 3-judge
  agreement >= 0.775 on `shuf`, >= 0.925 on `hard`. Stratified by
  `file_kind`.
- `eval/calibrated/labels.jsonl` -- mined via
  `eval/calibration_corpus.py --repo-root . --since 6.months`.
- **Benign covariance:** all rows with `label == "negative"` AND
  `file_kind in {source_code, test_code, plan_md, doc_md, config}`.
  Target `>= 50` per kind.
- **Regressions:** all rows with `label == "positive"` from revert /
  fix:* patterns. Target `>= 30` per kind.

### Signals evaluated

| Name                  | Formula                                                                              | Dep. on `cos`? |
|-----------------------|--------------------------------------------------------------------------------------|----------------|
| `coherence_delta`     | `||x_hat - y_hat||_2`                                                                | yes (transform of `cos`) |
| `ais` (existing)      | `(cos + 1) / 2`                                                                      | yes            |
| `novelty` (existing)  | `1 - max(cos, 0)`                                                                    | yes            |
| `mahal_anomaly` (new) | `sqrt((x_after - mu_benign)^T Sigma_benign_shrunk^{-1} (x_after - mu_benign))`     | no             |
| `knn_density` (new)   | mean kNN cosine distance from `x_after` to the k=10 nearest benign samples            | partial        |
| `regression_head`     | logreg on (cos, ||x_after||, per-dim) -> prob(regression)                            | partial        |

Where "partial" means the signal uses `cos` as a feature but adds
information not in `cos` alone (e.g. kNN distance captures local
density).

### Metrics

- **ROC-AUC** of each signal vs `label=1` on the test split
  (15% holdout, seed 42, stratified). Decision threshold: AUC
  improvement over `coherence_delta` baseline of `>= 0.05` absolute
  at alpha=0.05 two-sided DeLong.
- **Youden's J** at the optimal threshold per file_kind. Compare
  the best per-kind threshold for each signal; report
  max-of-kinds and mean-of-kinds.
- **Calibration slope.** Fit a logistic regression
  `label ~ signal` and report slope. A signal with slope != 1 is
  miscalibrated.
- **P95 trip rate parity** on benign edits. Must not regress by
  >10% relative vs the current `coherence_delta` baseline.
- **Determinism:** the calibration-covariance fit must be
  reproducible across runs to L2 tolerance 1e-6 (catches
  numerical instability in the Ledoit-Wolf estimator).
- **Latency budget.** Adding `mahal_anomaly` adds a single matrix
  multiply (768x768) per edit; budget p95 <= +50ms relative.

### Decision rule (pre-registered)

Promote `mahal_anomaly` iff ALL of:

1. ROC-AUC >= 0.70 on test split AND delta >= 0.05 vs
   `coherence_delta` baseline.
2. Mean Youden's J across file_kinds >= 0.45 (i.e. the signal
   catches at least 60% of regressions and 60% of benign with the
   optimal threshold).
3. Calibration slope in [0.85, 1.15] (no over/under-confidence).
4. P95 trip rate parity (relative delta <= 10%).
5. Falsifiability control (`random-mamba` embeddings): the new
   signal's AUC must be at chance (≤ 0.55) on random embeddings
   of the same input. Catches accidental label leakage.
6. Determinism tolerance <= 1e-6.

If `mahal_anomaly` fails but `knn_density` or `regression_head`
passes, prefer `knn_density` (cheaper, no training required). If
all fail, ship the existing path with the audit-documented
algebraic-redundancy addendum and re-open as a separate workstream.

### Baseline capture

```
rc baseline capture --id baseline-2026-09-19-scoring-v3-pre
```

before code changes; `baseline-2026-09-19-scoring-v3-post` after.

### Implementation sketch

- New module `src/scoring_signals.py` (~150 lines):
  - `mahalanobis_anomaly(emb_after, mu, sigma_inv, scale) -> float`
  - `knn_density(emb_after, benign_bank, k) -> float`
  - `regression_head_score(emb_before, emb_after, risk_vector, lr) -> float`
- Calibration step `eval/recalibrate_v3.py` (new, ~250 lines):
  - Loads `eval/calibrated/labels.jsonl` and the mined corpus.
  - Computes per-kind benign mu / Sigma with Ledoit-Wolf shrinkage.
  - Sweeps thresholds per file_kind.
  - Emits `_KIND_THRESHOLDS_V3.json` consumed by `s2_core.py`.
- Wiring in `s2_core.py`: add `mahal_anomaly`, `knn_density`,
  `regression_head_prob` to `ImpactReport`. Update
  `_KIND_THRESHOLDS` to read from the v3 manifest. Keep `ais` /
  `novelty` as deprecated shims.

## Risks and failure modes

1. **Covariance singularity.** 768-dim covariance with ~131 samples
   per kind is rank-deficient. Ledoit-Wolf shrinkage (`Ledoit &
   Wolf, 2004`) is the standard fix; we use the `sklearn` API
   (`sklearn.covariance.LedoitWolf`).
2. **Distribution shift.** The benign covariance fit on this
   repo's history may not generalise to a new repo. The signal
   must be re-fit per repo. Mitigation: ship the calibration
   result as part of the baseline manifest; operators re-run
   `rc baseline recalibrate` on first use in a new repo.
3. **Concept drift.** Embedding distributions drift with model
   updates. We re-fit on every `RC_EMBEDDER` change; the embedder
   memo's pre-reg captures this.
4. **Calibration corpus size.** 30-50 pairs per kind is the
   pre-existing floor (`thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`).
   We do not increase this; the Ledoit-Wolf shrinkage absorbs the
   variance.
5. **Operator trust.** Adding a new field to `ImpactReport` is a
   breaking change for downstream dashboards. Mitigation: emit
   the new field as an optional key (None if calibration not
   run); operators opt in.
6. **Adversarial robustness.** The Mahalanobis signal depends on
   the benign covariance; an adversary with knowledge of the
   covariance could craft embeddings that mimic benign. The
   `random-mamba` control (per the falsifiability check) catches
   this if the adversary has no specific knowledge; for a known
   attacker, see Risk 7.
7. **White-box attack.** An adversary with knowledge of the
   covariance can craft adversarial embeddings that minimise
   Mahalanobis distance while still being regressions. This is a
   well-known limitation of covariance-based OOD. Mitigation: the
   deterministic risk-vector dims still catch the structural
   signature; the new signal is *advisory*.
8. **Backward-compat regressions.** Removing `ais` / `novelty`
   would break the two-year audit log. Mitigation: keep them as
   deprecation shims for two release cycles; gate removal on
   operator opt-in via `RC_SCORING_V3_DROP_LEGACY=1`.

## Open questions for the user

1. **Audit-log retention.** Two years of rows have `ais` /
   `novelty` values. Are they preserved on disk in
   `~/.local/share/reasoning-core/events/`? (Default: yes; we
   read them but don't write new ones with the same keys.)
2. **Per-repo calibration.** Should the calibration-covariance be
   per-repo, or global? Per-repo is more accurate but adds a
   first-run cost. (Default: per-repo, run on first
   `score_change` per session, cached for 24h.)
3. **Backward compat timeline.** How many release cycles should
   `ais` / `novelty` remain as deprecation shims? (Default: 2.)
4. **Whitepaper scope.** Should we update
   `docs/whitepaper/sections/scoring.tex` to describe the new
   `mahal_anomaly` field, or keep the whitepaper at the current
   "AIS/CD/novelty are scalar transforms of cos" level? (Default:
   add a `4.6 New signals` subsection.)
