# CHANGELOG — 2026-06-01

This release lands 10 changes from the audit at
[`thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md`](../thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md).

## Behavior changes (defaults flipped or new caps)

- **`S2_HARD_CAP_MS=1500`** (new). Sidecar `/score` HTTP calls now cap
  at 1.5 s. On timeout the gate degrades to symbolic-only enforcement
  (rule_engine + lang_lock) and the audit event carries
  `reason="hard_cap_exceeded:<N>ms"`. Override via `S2_HARD_CAP_MS=N`
  in `.envrc.local`. Closes audit 2026-06-01 §1.4 (ssm p99 = 60 s).
- **`S2_COHERENCE_THRESHOLD=0.09`** (changed from `0.5`). Recalibrated
  to the empirical 95th percentile of the local audit corpus
  (audit §1.3). Per-kind floors in `s2_core._KIND_THRESHOLDS`
  adjusted to the chord-distance scale: `source_code=0.09`,
  `config=0.08`, `test_code=0.14`, `plan_md=0.30`, `doc_md=0.30`.
- **`RC_PLAN_GROUNDING=1`** (changed from `0`). Warn-only by default.
  Set `=0` to silence or `=2` to hard-block.
- **`RC_BEST_EFFORT_SPEC=1`** (changed from `0`). SessionStart spec
  overlay default-on.

## New surfaces

- **`gate_prm`** in `src/hooks/_dispatch.py` (signal_source `"prm"`).
  Measurement-only gate that calls `gen_client.score_plan_grounding`
  on each Edit when `RC_PRM_GATE=1`. Default OFF; emits audit events
  but never blocks. See audit §B1. Enforcement waits on a calibrated
  PRM trained from the corpus extractors below.
- **`session_id` fallback to `audit_log._session_id()`** when
  `CLAUDE_SESSION_ID` is unset. The Phase-2 risk dims
  (`session_centroid_drift`, `project_fan_in`, `project_coupling`)
  now actually fire on real hook invocations.
- **`gate_id` plumbed on every audit event.** Per-gate ablation queries
  on the audit log now work without hand-mapping `signal_source` →
  gate. Closes the gap reported in the 2026-05-23 audit
  §"What is not working" #1.
- **Auto-PLAN.md scaffold** at SessionStart when
  `RC_PLAN_GROUNDING ∈ {1, 2}` and no PLAN.md exists. Pulls a
  one-line goal from README.md. Honors `RC_NO_PLAN_SCAFFOLD=1`.
  Audit event: `signal_source="plan_scaffold"`,
  `reason="auto_scaffolded_from_readme"`.
- **`rc reasoning-efficiency`** new subcommand. Computes the §7
  composite north-star metric from the on-disk audit log —
  `(drift_caught − false_drifts) / (gate_wall_clock_s + 1)
  × repo_idiom_delta_norm × (1 − sidecar_unavailability_rate)`.
- **`gate_prm` audit-extra fields** `prm_score`, `prm_yes`,
  `prm_total` for downstream calibration.

## New tools / corpora

- **`scripts/risk_vector_correlation.py`** — empirical dim-redundancy
  tool over the audit log. Walks `*.jsonl[.gz]`, computes pairwise
  Pearson NaN-tolerantly, lists pairs with `|r| > 0.7`. No runtime
  impact — operator-invoked analysis.
- **`eval/build_prm_corpus.py`** — AgentPRM-style automatic-label
  extractor over iter-2 Claude sessions. Walks
  `<eval_root>/runs/<arm>/<task>/run-<N>/{plan.md, meta.json,
  diff_stats.json, safety.json}`, derives `step_label ∈ {-1, 0, +1}`
  from session outcomes (judge `correctness_determinism` grades by
  majority vote when `meta.resolved` is absent). On real iter-2 yields
  900 rows with all three step labels populated. Output gitignored at
  `eval/calibrated/prm_corpus.jsonl`.
- **`eval/calibration_corpus.py --include-positives`** (new flag,
  default on). Mines `fix:` / `Revert "..."` commits to add
  positive-label rows; the parent commit of a fix becomes the buggy
  sample. New `--no-positives` flag restores the original behavior.
  `--mode bisect` is stubbed for future work. First run against
  reasoning-core's own 6-month history added 42 positives.

## Migration notes

- **Existing installs**: re-run `direnv allow .` in any repo where
  reasoning-core is enabled so the new defaults take effect.
- **Iter-3 historical replays**: use the pinned
  [`docs/iter3-frozen-artifacts/eval-setups-A/envrc.txt`](iter3-frozen-artifacts/eval-setups-A/envrc.txt)
  which still carries the old `=0` defaults. Do NOT replay against
  the current default `.envrc` or A vs B is no longer apples-to-apples.
- **Slow sidecar regression**: if the new `S2_HARD_CAP_MS=1500` causes
  excessive `symbolic_fallback` audit events on a host with a cold
  Mamba boot, raise it: `S2_HARD_CAP_MS=5000` in `.envrc.local`.

## Sources

- Audit + improvement plan:
  [`thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md`](../thoughts/shared/research/2026-06-01-reasoning-core-1000pct-improvements.md)
- Prior audit:
  [`thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md`](../thoughts/shared/research/2026-05-23-reasoning-core-effectiveness-audit.md)
- PRM literature: AgentPRM
  [arXiv:2511.08325](https://arxiv.org/abs/2511.08325),
  Math-Shepherd
  [arXiv:2312.08935](https://arxiv.org/pdf/2312.08935),
  FreePRM [arXiv:2506.03570](https://arxiv.org/pdf/2506.03570),
  ThinkPRM [arXiv:2504.16828](https://arxiv.org/abs/2504.16828).
