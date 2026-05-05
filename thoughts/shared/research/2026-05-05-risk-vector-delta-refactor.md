---
date: 2026-05-05
commit: 2345fba
branch: main
tags: [risk-vector, calibration, false-positive, ssm]
status: complete
---
# Research: Absolute-vs-Delta Semantics in `_compute_risk_vector`

## Summary

5 of 8 risk dims (`fan_in`, `fan_out`, `depth`, `coupling`, `cohesion`) measure
**absolute properties of the after-state**, not the **delta** the edit
introduces. A small additive edit to a structurally-busy file therefore
saturates these dims to 1.0 and trips the global per-dim ceiling (`>0.9`) →
false-positive block. This is the same family of bug as the Edit-tool
reconstruction bug fixed in `2345fba`, but located inside the sidecar's risk
vector computation rather than the hook payload extraction.

## Files Involved

| File | Layer | Purpose |
|---|---|---|
| `src/s2_core.py:572-640` | Sidecar / scoring | `_compute_risk_vector` — current risk vector formulas |
| `src/s2_core.py:829-837` | Sidecar / orchestration | call site of `_compute_risk_vector` from `score_change` |
| `src/hooks/pre_edit_guard.py:90-128` | Hook | `_extract_changes` (Edit reconstruction fix already shipped in `2345fba`) |
| `tests/test_s2_core.py:228-260` | Tests | existing scoring tests — none cover small-edit-large-file |

## Per-Dim Audit

All locations in `src/s2_core.py`.

| Dim | Line | Inputs | Numerator | Scale | Semantic | Spurious saturation? |
|---|---|---|---|---|---|---|
| `cyclomatic` | 591–593 | both | `max(0, b_after − b_before) + 0.25 · b_after` | 20.0 | **hybrid** | YES — `b_after=80` ⇒ `0.25·80=20` saturates even when delta=0 |
| `fan_in` | 596–603 | after only | `max(in_counts_after.values())` | 8.0 | **absolute** | YES — file with 8+ callers on one node ⇒ 1.0 on any edit |
| `fan_out` | 602–604 | after only | `max(len(v) for v in graph_after.values())` | 12.0 | **absolute** | YES — function calling 12+ others ⇒ 1.0 |
| `depth` | 607–609 | both | `max(d_before, d_after)` | 40.0 | **max, not delta** | YES — depth-40+ file ⇒ 1.0 on any edit |
| `churn` | 612–617 | both | `len(symdiff(line_set_before, line_set_after))` | 200.0 | **delta** | only on real >200-line diffs |
| `coupling` | 620–621 | after only | `sum(len(v) for v in graph_after.values())` | 40.0 | **absolute** | YES — 40+ call edges ⇒ 1.0 |
| `cohesion` | 629–635 | after only | `isolated_nodes / total_nodes` (after-graph) | 1.0 | **absolute** | edge case only |
| `novelty` | 638–639 / 827 | both | `1 − max(cos(emb_b, emb_a), 0)` | 1.0 | **delta** | tiny edit ⇒ near 0 |

**Pure delta**: `churn`, `novelty` (2 of 8).
**Hybrid (delta + absolute leak)**: `cyclomatic` (1 of 8).
**Absolute (after-state only)**: `fan_in`, `fan_out`, `coupling`, `cohesion`, plus `depth` which is `max(before, after)` so behaves like absolute (4–5 of 8).

## Data Flow at `score_change`

`src/s2_core.py:785-837`:

1. `parse_before = parse_source(path, before_src)` → tree-sitter CST.
2. `parse_after = parse_source(path, after_src)` → same shape.
3. `graph_before = build_call_graph(parse_before.tree, before_src, lang)` → `dict[str, set[str]]`.
4. `graph_after = build_call_graph(...)` → same.
5. `novelty = max(0, min(1, 1 − max(cos, 0)))` (line 827).
6. `_compute_risk_vector(parse_before, parse_after, graph_before, graph_after, novelty, before_src, after_src)`.

Both `graph_before` and `parse_before` are **available** — they are simply not used by 5 of the 8 dims.

## Observed Production Block

`14:50:10`: Edit on `circit-app/Circit.Website/Controllers/TestController.cs`
(test_code kind):

```
AIS:             1.00  (thr 0.40)
coherence_delta: 0.00  (thr 2.00)   ← cold-start fired
top contributors:
  fan_out=1.00   ← graph_after has a function calling 12+ others
  coupling=1.00  ← graph_after has 40+ total edges
  depth=0.82
```

The Edit was a 1-line additive feature-flag pass-through. Pre-edit and post-edit
files have nearly identical call graphs. The dims should have measured ~0
delta. Because they measure absolute after-state, they saturated.

## Existing Patterns Found

- `cyclomatic` already uses `b_after − b_before` for the delta portion (line
  591). The pattern of subtracting before-state from after-state is in the
  file already; just not consistently applied.
- `churn` already uses `set_before.symmetric_difference(set_after)`. Pure
  delta semantics are the established norm, not a novel pattern to introduce.
- No existing test verifies that a small additive edit to a busy file
  produces small dims. `tests/test_s2_core.py:244` (`test_score_change_regression_detected`)
  uses a 25-branch cyclomatic explosion — unrelated to delta semantics.

## Architecture Notes

- `_compute_risk_vector` already receives both `graph_before` and `graph_after`
  (line 829-837). No new wiring needed — the absolute-only dims just need to
  reference both.
- The cohesion fix shipped in commit `8a2c352` (per CLAUDE summary) was a
  separate bug — handling the `nodes < 2` edge case. Independent of this.
- Per-kind risk-dim ceilings (e.g. allowing `test_code` to tolerate higher
  `fan_out`) are an orthogonal mitigation. Either approach (delta refactor OR
  per-kind ceiling) closes the false-positive but for different reasons:
  - Delta refactor: dims accurately measure change, ceiling 0.9 stays correct.
  - Per-kind ceiling: dims still measure absolutes, but ceiling raised so
    inherent file complexity doesn't trip a global threshold.

The delta refactor is the more correct fix because it preserves the original
intent of the metrics (measure regression introduced by the edit) and doesn't
hide real regressions on test code that genuinely saturate from a sprawling
edit.

## Open Questions

1. **Cohesion delta semantics** — for cohesion, what is the right delta? Naive
   `delta = cohesion_after − cohesion_before` would let an edit that maintains
   high lack-of-cohesion pass freely. Options:
   - `delta = max(cohesion_after − cohesion_before, 0)` — only penalize
     making cohesion worse.
   - Keep absolute but raise scale (e.g. 2.0) so it rarely saturates.
   - Drop from regression rule on edit-time and use only on initial ingest.
2. **Depth** — `max(d_before, d_after)` is asymmetric. A delta version
   `max(d_after − d_before, 0)` matches the spirit but allows a deeply
   nested file to receive any edit. Probably correct for edit-time scoring.
3. **Cyclomatic absolute-leak `0.25 · b_after`** — the original intent was
   probably "penalize files that are absolutely complex even if delta=0".
   Removing the leak removes that signal. Worth keeping a small leak (e.g.
   `0.05 · b_after`) for discoverability without dominating the score.

## Recommendation

Ship a delta-semantics refactor of `_compute_risk_vector`:

- `fan_in_delta = max(after_max_indeg − before_max_indeg, 0)`
- `fan_out_delta = max(after_max_outdeg − before_max_outdeg, 0)`
- `depth_delta = max(d_after − d_before, 0)`
- `coupling_delta = max(edges_after − edges_before, 0)`
- `cohesion_delta = max(after_lack_of_cohesion − before_lack_of_cohesion, 0)`
- `cyclomatic`: drop the `0.25 · b_after` leak OR shrink to `0.05 · b_after`.

Add tests:
- `test_small_edit_on_busy_file_keeps_dims_low` — 200-line file, 1-line
  additive edit → all dims < 0.2.
- `test_full_rewrite_saturates_dims` — fresh write of busy file →
  `regression_detected=True`.
- `test_legitimate_fan_out_explosion_still_detected` — edit that adds 10
  outbound calls → `fan_out` still rises proportionally.

## Sources

- `src/s2_core.py:572-640` (current implementation)
- `src/s2_core.py:785-837` (call site)
- `tests/test_s2_core.py:228-260` (existing tests)
- Live audit log: `/tmp/rc-events/2026-05-05/` row at `14:50:10` showing
  fan_out=1.00 / coupling=1.00 false positive on TestController.cs
- Prior research: `thoughts/shared/research/2026-05-05-coherence-delta-calibration.md`
- Prior fix: commit `2345fba` (Edit-tool reconstruction)
