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

## Follow-up commits

- **`54c7aad fix(_plan_scaffold)`** — the auto-PLAN.md scaffold now
  refuses to write into the reasoning-core repo itself. Found via
  verification: a sibling test invoked `session_start_best_effort.main()`
  from cwd=repo and the scaffold dropped a stray top-level `PLAN.md`,
  which broke `test_iter3_wiring_smoke::test_smoke_plan_grounding_audit_on_missing_plan`
  (the hook walked up and found the stray file instead of seeing
  "no PLAN.md"). The helper now detects the marker file
  `src/hooks/_plan_scaffold.py` under `project_dir` and skips when it
  resolves to itself.
- **`929789f fix(tests)`** — `tests/test_sidecar_hard_cap.py` stub
  now suppresses `BrokenPipeError` / `ConnectionResetError` on read
  and response-write. The hard-cap path under test deliberately
  abandons the connection at 500ms while the stub is still sleeping;
  the BrokenPipe trace was harmless but noisy in CI.

## Verification observations (2026-06-02)

Ran the 5 user-visible surfaces directly (not via the test suite):

| Surface | Observation |
|---|---|
| `pre_edit_guard` subprocess + slow stub | Hook exits 0 in 0.70s vs the 5s stub delay; stderr emits `sidecar hard cap exceeded (500ms); symbolic fallback engaged`. ✅ |
| `rc reasoning-efficiency` | 3089-event audit log → composite metric prints with stable schema. ✅ |
| `scripts/risk_vector_correlation.py` | 3089 events → 8×8 correlation populated, **three redundant pairs surfaced**: `fan_in↔fan_out r=+0.708`, `fan_in↔depth r=+0.876`, `fan_out↔depth r=+0.757`. The Phase-2 dim columns (`session_centroid_drift`, `project_fan_in`, `project_coupling`) are **all-NaN today** because they never fired in historical audit data — they only start firing after U3 (session_id fallback) is in effect. Re-run after 14 days of production traffic to confirm those dims become non-degenerate. |
| `eval/build_prm_corpus.py` | Real iter-2 corpus → 900 rows, label distribution {-1:360, 0:260, +1:280}. Schema fits the AgentPRM training contract. ✅ |
| `eval/calibration_corpus.py --include-positives` | Reasoning-core's own 1-month history → 39 positives sourced from 16 distinct `fix_parent:<sha>` commits. One positive sample points at `src/hooks/pre_edit_guard.py` from commit `b633cd1` (this batch's U3 fix) — the corpus is self-feeding. ✅ |

## Known follow-ups

- **Re-run `scripts/risk_vector_correlation.py` ~2026-06-15** to
  confirm Phase-2 dims start firing in production audit data after
  U3 + U5 unlock them. If they remain NaN, U5.i (session_id) didn't
  reach `/score` in real Claude Code sessions and needs a different
  fix.
- **Train a PRM on the corpus extractors' output** before flipping
  `RC_PRM_GATE=1` to default-on. Calibration thresholds will follow
  from training, not from heuristics.
- **Re-mine positives quarterly** — `eval/calibration_corpus.py
  --include-positives` produces growing yield as the repo accumulates
  more fix/revert commits.

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
