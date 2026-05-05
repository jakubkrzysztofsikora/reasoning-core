---
date: 2026-05-05
branch: main
status: complete
tags: [calibration, ssm, coherence-delta, thresholds, drift-detection]
---
# Research: Coherence-Delta Calibration & Cold-Start Mitigation

## Question

The `coherence_delta` gate in `src/s2_core.py` was mis-calibrated for two
real-world cases observed in production:

1. **New-file Writes** (`before_src=""`) almost always exceeded the 1.5
   threshold because `embed("") - embed(content)` has large L2 regardless
   of content quality.
2. **Plan markdown** (`thoughts/shared/plans/*.md`) tripped the threshold
   on legitimate large plans because the threshold was calibrated for
   code-edit deltas, not prose distribution.

How do we calibrate so the gate only fires on genuine architectural
regressions, not file births and prose plans?

## Status (2026-05-05)

Phases 1-3 of the recommended plan shipped in commits `f5f4a8d`,
`1c27bdd`, `af48253`. Phase 4 (block-message helper) shipped in
`e68a795`. Phases 5+ (cosine-distance refactor, bootstrap calibration)
deferred to follow-up sprints.

## Original Implementation (pre-sprint, file:line)

| Aspect | Location | Value |
|---|---|---|
| `coherence_delta` formula | `src/s2_core.py:768-769` | `raw_l2 / sqrt(hidden_size)` |
| `COHERENCE_DELTA_THRESHOLD` | `src/s2_core.py:652` | `1.5` (hardcoded) |
| `_REGRESSION_AIS_THRESHOLD` | `src/s2_core.py:647` | `0.4` |
| `_REGRESSION_RISK_DIM_THRESHOLD` | `src/s2_core.py` | `0.9` |
| Regression rule | `src/s2_core.py:784-788` | `AIS<0.4 OR cd>1.5 OR any dim>0.9` |
| `before_src=""` handling | `src/s2_core.py:737-749` | parsed empty, embedded as pad/near-zero |
| Per-kind dispatch | none | global thresholds for all langs/kinds |
| Existing env knobs | `S2_PORT`, `S2_LOG_LEVEL` only | no threshold overrides |
| `cumulative_drift` | `src/s2_core.py:800-804` | computed, never gated (line 729-731) |

## Findings

### 1. Empty-baseline cold-start is well-known

Industry default for new-file events is **skip with structured signal**,
not score against zero. Sources: Evidently AI drift guide, ACL 2024
"From Zero to Hero" cold-start anomaly paper, Google anomaly-detection
cookbook.

### 2. Magnitude vs direction

`raw_l2 / sqrt(hidden_size)` corrects for dimensionality but not for
embedding-space variance per content type. Code tokens cluster in a
narrow syntactic subspace; prose fills a broader manifold. Cosine
distance on L2-normalized vectors removes magnitude entirely.

### 3. Per-kind dispatch matches the SonarQube pattern

SonarQube ships separate quality profiles per language. Translates to
`THRESHOLDS = {kind: {cd, ais}}` keyed off `file_kind` before scoring.

### 4. Calibration corpus needs are modest

For a single-developer project, 30-50 benign before/after pairs per
content type suffice for P95 bootstrap. ROC tuning needs labels.

### 5. Plumbing was already there

`cumulative_drift` (POST `/baseline`, session-keyed mean embeddings,
distance computation) existed pre-sprint. Per-kind dispatch was the
smaller change.

## Decisions Made

### Phase 1 — Cold-start skip ✓ shipped (`f5f4a8d`)

`score_change()` checks `before_src.strip()` and length < 32; if so,
sets `coherence_delta = 0.0` and stamps `cold_start=True` on the
report. The 8-dim risk vector still computes — content remains
gateable on churn / cyclomatic / cohesion etc.

### Phase 2 — Env-overridable thresholds ✓ shipped (`1c27bdd`)

`S2_AIS_THRESHOLD`, `S2_COHERENCE_THRESHOLD`, `S2_RISK_DIM_THRESHOLD`
read at startup via `_env_float`, default to prior hardcoded values.
Documented in `.envrc` comment block.

### Phase 3 — Per-file-kind dispatch ✓ shipped (`af48253`)

`_file_kind(path)` routes by suffix and path heuristics:

| kind | cd | ais |
|---|---:|---:|
| source_code | 1.5 | 0.4 |
| test_code   | 2.0 | 0.3 |
| plan_md     | 3.0 | 0.3 |
| doc_md      | 3.0 | 0.3 |
| config      | 1.2 | 0.5 |

Per-risk-dim ceiling unchanged: 0.9. ImpactReport surfaces `file_kind`
and resolved `cd_threshold`.

### Phase 4 — Block-message helper ✓ shipped (`e68a795`)

`src/hooks/_block_format.py` produces a stderr block with top-3 risk
contributors and per-dim repair hints (`HINTS` dict). Adds a
`RETRY DETECTED` banner when `audit_log.is_retry_after_block` is true,
plus a `GUIDANCE` footer telling Claude that bypass attempts will also
be blocked.

## Trade-offs Accepted

| Choice | Gives up |
|---|---|
| Cold-start skip at <32 chars | False-negatives on tiny garbage files (mitigated by risk_vector) |
| Per-kind dispatch | More config surface; thresholds drift over time |
| Hardcoded non-source thresholds | Operator must edit `_KIND_THRESHOLDS` to retune; no env knob per kind yet |

## Follow-Ups (Not Shipped)

- Cosine-distance refactor: `1 - cos_sim(F.normalize(a), F.normalize(b))`
  ranged in [0, 2]. Requires re-tuning per-kind thresholds.
- Bootstrap calibration script `eval/calibrate_thresholds.py`: walks git
  history, computes P95 per kind, writes
  `eval/calibrated_thresholds.json`.
- Per-kind env knobs: `S2_KIND_THRESHOLDS` JSON env var.
- ROC F1-max calibration: requires labeled good/bad corpus.

## Sources

- [Evidently AI — 5 Methods to Detect Embedding Drift](https://www.evidentlyai.com/blog/embedding-drift-detection)
- [Drift Detection in LLMs: Practical Guide](https://medium.com/@tsiciliani/drift-detection-in-large-language-models-a-practical-guide-3f54d783792c)
- [From Zero to Hero: Cold-Start Anomaly Detection — ACL 2024](https://aclanthology.org/2024.findings-acl.453.pdf)
- [DriftLens — EDBT 2024](https://openproceedings.org/2024/conf/edbt/paper-239.pdf)
- [Anomaly Detection with Embeddings — Google Cookbook](https://github.com/google-gemini/cookbook/blob/main/examples/Anomaly_detection_with_embeddings.ipynb)
- [SonarQube Metrics Definitions](https://docs.sonarsource.com/sonarqube-server/user-guide/code-metrics/metrics-definition)
- [L2 vs Cosine Similarity](https://medium.com/@ishankgera.work/l2-distance-vs-cosine-similarity-the-hidden-connection-35c1ae121392)
- Internal: `docs/HARDENING.md` § Known residual gaps #4
- Internal: handoff `thoughts/shared/handoffs/2026-05-05-calib-sprint.md`
