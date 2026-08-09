# Kimi × reasoning-core — Live Gate Validation (2026-07-22)

Independent live validation of the reasoning-core enforcement path, run by Kimi (Moonshot AI)
as host model in both arms of a pre-registered vanilla-vs-gated A/B protocol, against the real
scoring stack at commit 54dc16e (full Mamba-130M neural path, CPU).

## Contents
- `EXPERIMENT_REPORT.md` — full report (setup, detection suite, A/B arms, model-independence, simulation, limitations, verdict)
- `PREREGISTRATION.md` — protocol frozen before any arm ran (task pairs, metrics, decision rule, randomized assignment)
- `AMENDMENT-1.md` — plan-adherence check added pre-scenario-suite
- `AMENDMENT-2.md` — silently-dead ruff oracle incident (bad `--no-color` flag, exit 2 -> zero fails); caught by the canary suite
- `CALIBRATION.json` — shadow-mode coherence calibration and frozen cap (0.10)
- `gate_worker.py` — loopback gate worker: oracles (ast.parse/py_compile/ruff/rules.yaml) + neural score_change + plan check
- `gated_write.py` — write-only-on-ALLOW helper used by the gated arm
- `assignment.json` — randomized arm assignment (seed 20260722)
- `scenario_results_v2.json` — 14-scenario detection suite results (fixed gate)
- `scenario_results_v1_brokenruff.json` — preserved results with the dead lint oracle (audit trail)
- `armA_posthoc_gate_audit.json` — all 16 vanilla-arm edits replayed through the fixed gate: 8/16 would have been blocked
- `model_independence.csv` — 27 gate calls (3 edits x 3 model labels x 3 reps), bit-identical verdicts
- `stream_simulation.json` — Monte-Carlo streams (measured detection rate 0.90), guarded vs unguarded

## Headline results
- Detection: 9/10 planted bad-edit classes blocked at write time; 0 false blocks on genuinely clean edits
- Vanilla audit: 50% (8/16) of unguarded edits carried gate-catchable violations, discovered late or never
- Model-independence: verdicts bit-identical across simulated model sources (gate contract has no model input)
- Variance compression: strong-vs-weak shipped-defect spread 31.3 pts unguarded -> 3.1 pts guarded (~10x), weak stream pays ~1.45x rework
- Upstream suite: 605 passed, 1 skipped (pytest -m "not live and not slow")

## Honest scope
Single conversation with paired task variants (contamination mitigated, not eliminated);
14 hand-authored scenarios; coherence cap calibrated on 4 clean edits; the circular-import
ship and the borderline cd=0.112 block show the frozen config's blind spots. The agent-side
cross-model arm (Fable/GLM/Qwen/Kimi each ± gate) requires hosted API access and remains open;
the white paper's §6 protocol has its instrumentation validated end-to-end here.

Companion white paper: "The Model Doesn't Matter (As Much As You Think)" (addendum references this data).
