# Amendment 2 (2026-07-22, post-scenario-suite-v1, pre-v2)
Root cause: gate_worker passed `--no-color` to ruff; installed ruff rejects it
(exit 2), leaving the lint oracle SILENTLY DEAD (no stdout -> zero fails).
The scenario suite itself detected this ("ruff unused-import" expected BLOCK, got ALLOW).
Fix: drop the flag. All scenario results re-run with the corrected gate; v1 results
preserved in scenario_results_v1_brokenruff.json for the audit trail.
Meta-finding: a guard whose oracle dies silently is worse than no guard; the
experiment's expected-BLOCK canary suite is what caught it. Recommend upstream
add an oracle self-test at sidecar boot.
