# Evaluation handoff

Before changing a threshold, quality claim, enforcement default, evaluation
criterion, or model backend, read `eval/baselines/README.md` and inspect the
active baseline with `rc baseline show baseline-2026-08-09`. Record a new,
immutable baseline before making a comparison claim.

Deterministic policy, plan, parse/lint, and structural checks are the only
standalone hard-block sources. Neural scoring is advisory unless corroborated
by an independent deterministic signal. Do not represent synthetic scenarios,
historical telemetry, or a small weekly run as broad causal evidence.
