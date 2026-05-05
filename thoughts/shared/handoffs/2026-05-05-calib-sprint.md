---
date: 2026-05-05
sprint: coherence_delta calibration
status: shipped
commits: f5f4a8d, 1c27bdd, af48253, e68a795
---

# Calibration sprint — 2026-05-05

## What shipped

| Phase | Commit | Summary |
|---|---|---|
| 1 | `f5f4a8d` | Cold-start skip in `score_change`; ImpactReport gains `cold_start`/`file_kind`/`cd_threshold`. |
| 2 | `1c27bdd` | Env-overridable thresholds: `S2_AIS_THRESHOLD`, `S2_COHERENCE_THRESHOLD`, `S2_RISK_DIM_THRESHOLD`. Documented in `.envrc`. |
| 3 | `af48253` | Per-file-kind dispatch via `_file_kind()` and `_KIND_THRESHOLDS`. |
| 4 | `e68a795` | Block-message helper `src/hooks/_block_format.py`: top-3 contributors with repair hints + RETRY banner. |

## Threshold table

| kind | coherence_delta | AIS | cd source |
|---|---:|---:|---|
| source_code | 1.5 | 0.4 | env-overridable (`S2_COHERENCE_THRESHOLD` / `S2_AIS_THRESHOLD`) |
| test_code   | 2.0 | 0.3 | hardcoded in `_KIND_THRESHOLDS` |
| plan_md     | 3.0 | 0.3 | hardcoded |
| doc_md      | 3.0 | 0.3 | hardcoded |
| config      | 1.2 | 0.5 | hardcoded |

Per-risk-dim ceiling unchanged: 0.9 (`S2_RISK_DIM_THRESHOLD`).

Cold-start (empty or `<32` char `before_src`) sets `coherence_delta=0.0` and the
8-dim risk_vector still gates on churn / cyclomatic / cohesion etc.

## Tests

`pytest -m "not live"` → **171 passed, 2 failed**.

The two failures (`test_pre_bash_guard::test_screen_command_unit`,
`test_pre_task_guard::test_screen_prompt_mutation_verb_on_guarded_path`) are
**pre-existing** — verified by stashing the sprint changes and running tests
before phase 1 began. They are not regressions from this sprint and are out of
scope.

## Smoke verifications

- New plan markdown (5000 char body, empty before): `cold_start=true`,
  `cd=0.0`, `kind=plan_md`, `cd_threshold=3.0`, `regression=false` ✓
- 2000-line garbage Python new file: `cold_start=true`, churn=1.0 + cohesion=1.0
  trip per-dim threshold → `regression=true` ✓ (content can still block on risk_vector)
- Block message helper: shows file_kind, per-kind cd threshold, top-3 risk
  contributors with repair hints, RETRY banner when `is_retry=true` ✓

## What was NOT shipped (follow-ups)

- **Phase 4 of the spec — cosine-distance refactor**: deferred to a separate
  sprint, needs full re-calibration of the `cd` thresholds against the new
  metric.
- **Phase 5 of the spec — bootstrap calibration script**
  (`eval/calibrate_thresholds.py`): needs a representative corpus first.

## Pre-existing dirty state pulled into the sprint

The working tree included unrelated edits (`.envrc` y-router gating,
`scripts/start-sidecar.sh` venv selection, an earlier botched
`_block_format.py`). The botched `_block_format.py` was rewritten in phase 4.
The `.envrc` and `start-sidecar.sh` changes were left untouched and shipped
alongside their respective sprint phases — operator confirmed during the
sprint that they were OK to include.

## Sidecar state

```
$ curl -fsS http://127.0.0.1:8765/health | jq .model_loaded
true
```

Backbone: `state-spaces/mamba-130m-hf` (768-D), CPU.
