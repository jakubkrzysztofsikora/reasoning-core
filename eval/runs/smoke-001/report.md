# Eval Report -- smoke-001

- pairs: 2
- generated_at: 2026-05-01T00:36:30.048463+00:00

## Metrics (treatment - vanilla)

| metric | n | mean_delta | 95% CI | p (wilcoxon) | p (holm) |
|---|---:|---:|---|---:|---:|
| resolved_rate | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| regression_rate | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| ast_edit_distance | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| cyclomatic_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| fan_in_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| fan_out_delta | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| wall_clock_s | 2 | -0.5000 | [-1.000, -0.500] | 1.0000 | 1.0000 |
| tokens_in | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| tokens_out | 2 | 0.0000 | [0.000, 0.000] | nan | nan |
| novelty_drift | 2 | 0.0000 | [0.000, 0.000] | nan | nan |

## Decision criteria

- verdict: **inconclusive**
- regression_rate_drop_geq_0.15_holm_p_lt_0.05: FAIL
- resolved_rate_no_worse_than_-5pp: PASS
- latency_ratio_leq_1.5: PASS

Operational counts: {"tasks_seen": 2, "complete_pairs": 2, "audit_events": 311, "audit_blocks": 0}
