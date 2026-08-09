# Amendment 1 (2026-07-22, post-Arm-B, pre-scenario-suite)
Add deterministic PLAN-ADHERENCE check to the gate: each task declares its file set;
a proposed write outside the set = BLOCK. Mirrors upstream PLAN.md plan-implementation
alignment gating (docs/USAGE.md). No change to oracles, neural cap, or metric definitions.
