# Plan: reasoning-core Kimi Setup + Reviewer Points 1-6 Implementation

## Context
- Repo: `/mnt/agents/reasoning-core` (branch: `claude/install-reasoning-core-oJft4`)
- Target: Adapt installation for Kimi (not Claude Code), test, then implement reviewer fix plan
- Environment: System Python 3.12.12 (venv creation blocked by filesystem limits)
- Dependencies: Partially installed (fastapi, torch OK; transformers installed but v5.8.1 > spec)

## Stage 1 — Environment + Kimi Setup
- Install missing deps (tree-sitter grammars, pytest, etc.) — MOSTLY DONE
- Create Kimi adapter (`src/hooks/adapters/kimi.py`) following the Claude/Gemini/Vibe pattern
- Create Kimi-specific configuration (`.kimi/settings.json` + `.kimi/skills/reasoning/SKILL.md`)
- Create `scripts/enable-in-repo-kimi.sh` installer
- Verify imports and run fast unit tests

## Stage 2 — Phase 0: Backbone Swap (Precursor)
- Extend `src/ssm_backbone.py` `_load_backbone` to accept `RC_EMBEDDER` env
- Support arms: `codestral-mamba` (default), `mamba-130m` (legacy), `bge-code`, `random-mamba`
- Adjust `MAX_SEQ_LEN` per embedder (8192 for Codestral, 512 for legacy)
- Expose `backbone_model` + `embedder_role` in `/health`
- Run embedder validation if feasible

## Stage 3 — Phase 1: Ablation Harness + Embedder Comparison
- `eval/preregistration/2026-05-13.md` — pre-registered null hypothesis
- `eval/run_ablation.py` — 8-arm factorial with paired-bootstrap CIs
- `eval/compare_embedders.py` — 5 embedder arms with z-rescale
- `gate_id` field in audit log (schema v2 minor bump)

## Stage 4 — Phase 2: Atomic Schema Bump + New Dims
- Single commit: `session_centroid_drift` + `project_fan_in` + `project_coupling`
- `risk_labels_version: 2` on ImpactReport + audit log
- `_calibration_gate.py` auto-invalidate on dim mismatch
- Fix 5 `== 8` assertions in tests
- `scripts/migrate_audit_log_v1_to_v2.py`

## Stage 5 — Phase 3: Consensus + Honest Attribution
- `consensus_score` field when `RC_CONSENSUS=1`
- `fired_conditions` / `fired_dims` / `fired_margins` on ImpactReport
- Code-vocabulary cleanup (no README changes per user)

## Stage 6 — Phase 4: Rule Engine
- `.reasoning-core/rules.yaml` + `.reasoning-core/rules.schema.yaml`
- `src/hooks/_rule_engine.py` (~900 LOC)
- `gate_rule_engine` slot in `_dispatch.py`
- 4 dogfood rules (Python + JS/TS)
- `RC_RULE_ENGINE_LENIENT=1` escape hatch

## Skill Loading
- Stage 1-2: `vibecoding-general-swarm` (Mode B for focused tasks, Mode A for parallel modules)
- Stages 3-6: Same skill, progressively loaded

## Test Strategy
- Fast unit tests (no backbone load): `pytest tests/ -m "not live" -k "not (backbone or ssm)"`
- Smoke tests with stub backbone
- Subagent verification for cross-file contracts
