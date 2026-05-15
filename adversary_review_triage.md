# Adversary Review Triage — reasoning-core

## CRITICAL (MUST FIX)

| ID | Finding | Source | Fix Effort |
|----|---------|--------|------------|
| C-DS1 | Pre-registered H0 untestable: cross-arm contrast (scorer_only vs plan_grounding_only) never computed | DS Scientist | Medium |
| C-DS3 | Spearman rho loader broken: glob pattern mismatches actual output path | DS Scientist | Small |
| C-ENG3 | `break` after FIRST dim breach loses all subsequent breached dims | Senior Eng | Small |
| C-ENG4 | cumulative_drift in risk_vector[8] triggers dim_ceiling spuriously | Senior Eng | Small |
| C-SEC1 | Shell injection via unquoted `$TASK_ID` in run_task.sh heredoc | Security | Small |
| C-INT1 | ImpactReport.to_dict() conditionally omits fields, breaking downstream consumers | Integration | Small |

## HIGH (SHOULD FIX)

| ID | Finding | Source | Fix Effort |
|----|---------|--------|------------|
| H-ENG1 | Unbounded _load_cache memory leak | Senior Eng | Small |
| H-ENG2 | _CONSENSUS_HANDLE never released (GPU VRAM leak) | Senior Eng | Small |
| H-ENG3 | Regex matches commented-out imports (false positives) | Senior Eng | Medium |
| H-ENG5 | gate_consensus() has zero test coverage | Senior Eng | Medium |
| H-INT1 | gate_rule_engine() import fails in hook subprocess (fail-open) | Integration | Small |
| H-INT2 | Test files hardcode 8-dim risk_vector | Integration | Small |
| H-INT3 | rc_cli.py _KNOBS missing 8 env vars | Integration | Small |

## Batch Plan
Batch 1: C-ENG3, C-ENG4, C-INT1, H-INT2 (s2_core.py + tests) — 1 agent
Batch 2: C-DS1, C-DS3 (run_ablation.py + compare_embedders.py) — 1 agent
Batch 3: C-SEC1 (run_task.sh) — inline edit
Batch 4: H-ENG1, H-ENG2, H-ENG3, H-INT1, H-INT3 (_rule_engine.py + _dispatch.py + rc_cli.py) — 1 agent
Batch 5: H-ENG5 (test_consensus.py + gate_consensus tests) — 1 agent
