---
date: 2026-05-06
commit: 692255f
branch: main
ticket: next-up-phase0-and-3.5
status: ready-to-execute
informed_by:
  - thoughts/shared/plans/2026-05-06-system-2-loop-closure.md (v6)
  - 4-reviewer MVP triage (2026-05-06): all 4 converge on "Phase 0 + 3.5 highest leverage"
  - 4-agent deep research on MLX port (deferred: 2-4× speedup not 5-10×; calibration re-fit cost real)
  - 2-agent execution-plan synthesis (Phase 0, Phase 3.5)
---
# Simple Plan: Phase 0 + Phase 3.5 (Next 2 Working Days)

## Why these two

4-reviewer MVP triage converged: **Phase 0 (split god-file) + Phase 3.5
(v3 cross-family kappa) are the two highest-leverage unblockers**.

- Phase 0 unblocks Phases 1-3 of loop-closure plan (rule engine, SCR, etc.)
- Phase 3.5 unblocks SCR auto-apply, Mamba-3 cutover, calibration promotion
  simultaneously
- Everything else (MLX port, cloud-GPU sidecar, rule engine, SCR, TTFV
  installer) deferred until these land

Total estimate: ~15-16h active dev = ~2 working days for solo dev.

## What's deferred (explicit no-ship list)

- **Phase 1** (TTFV + rc audit-history + npx installer) — defer
- **Phase 2** (rule engine + .reasoning-core/rules.yaml) — defer
- **Phase 3** (SCR loop) — defer; gates on Phase 3.5 anyway
- **Phase 3.6** (30-pair ground-truth corpus) — defer; parallel track
- **Phase 4** (Mamba-2 Plan-B, Mamba-3 watch) — defer; gates on Phase 3.5
- **MLX port** — deferred per round-3 deep research (2-4× actual speedup,
  parity risk RISKY, calibration re-fit cost ~1-2h, mlx-lm Mamba is
  Python-loop scan with documented Metal non-determinism). Reframe as
  Phase-4 Plan-C for later iteration.
- **Cloud-GPU sidecar** — better p95 win (300-700ms) but moves hot path
  through network; defer for after Phase 3 SCR ships.

---

## Phase 0: Split pre_edit_guard.py + promote git_utils (Day 1, ~9h)

`pre_edit_guard.py` is currently 765 LOC (god file untestable past 750).
Senior-dev round-2 flagged this as CRITICAL. Plan v6 prescribes the split.

### Branch: `phase-0/guard-split`

### Commit 1 — Extract `_guard_paths.py` (~2.5h)

**Created**: `src/hooks/_guard_paths.py` (~110 LOC)
**Modified**: `src/hooks/pre_edit_guard.py` (-45 LOC -> ~720 LOC)

Lift `GUARDED_PATHS` tuple (lines 304-347) and substring-match block
(lines 354-374) out. New API:
```python
GUARDED_PATHS: tuple[str, ...]                        # frozen at import
def discover(project_root: Path) -> tuple[str, ...]   # glob src/hooks/*.py + scripts/start-*sidecar.sh + explicit allowlist
def is_guarded(file_path: str) -> bool                # abspath + substring match (uses round-2 fix)
def is_override_active() -> bool                      # reads RC_ALLOW_GUARD_EDIT
```

Globs at import: `src/hooks/*.py`, `scripts/start-*sidecar.sh` + explicit
allowlist for `src/_supervisor*.py` / `src/calibration.py` / `src/gen_client.py`
/ `src/sidecar_supervisor.py` / `src/s2_core.py` / `src/grammars.py` /
`src/ssm_backbone.py` / `src/mcp_reasoner.py` / `src/rc_cli.py`. Filter
`_*test*.py` from glob results.

Override env: `RC_GUARD_PATHS_EXTRA` appends; `RC_GUARD_PATHS_OVERRIDE`
replaces (test-only).

**Tests**: existing `tests/test_guard_files_locked.py` + `tests/test_pre_edit_guard_*`
must stay green. New: `tests/test_guard_paths.py` covering discovery,
override env, abspath normalization.

**Acceptance**: `pre_edit_guard.py` <= 720 LOC. Suite green. Existing test
that walks every original GUARDED_PATHS entry still passes (no slip).

### Commit 2 — Extract `_dispatch.py` (~4h, highest risk)

**Created**: `src/hooks/_dispatch.py` (~170 LOC)
**Modified**: `src/hooks/pre_edit_guard.py` (-180 LOC -> ~540 LOC)

Each gate -> function returning `GateOutcome(decision, code, stderr, audit_kwargs)`.
Extracted gates and source ranges:

| Gate | Source range |
|---|---|
| `gate_kill_switch_and_magic` | lines 385-438 |
| `gate_lang_lock` | lines 445-485 |
| `gate_mock_detector` | lines 559-588 |
| `gate_drift` | lines 600-651 |
| `gate_calibration` | lines 658-705 |
| `gate_regression` | lines 707-743 |

`_dispatch.run_pre_score_gates(...)` runs first three; `run_post_score_gates(...)`
runs mock -> drift -> calibration -> regression on each (before, after) pair.

`pre_edit_guard.main()` becomes thin orchestrator: payload -> guard-paths ->
pre-score -> for pair: post_score -> score sidecar -> post-score gates ->
emit allowed.

**Audit emission stays in pre_edit_guard** (gate functions return
`audit_kwargs`, caller emits). Avoids `_dispatch` -> `audit_log` cycle.

**Import edge**: `_dispatch` imports `_calibration_gate` / `_kill_switches`
/ `_magic_comments` / `_mock_detector` / `_session_manifest` / `_shadow_mode`.
`_dispatch` does NOT import `pre_edit_guard`. One-way.

**Tests**: All `tests/test_pre_edit_guard_*` must remain green unchanged
(`main()` external behavior byte-identical). No new tests required;
optional `tests/test_dispatch_smoke.py` if coverage drops.

**Acceptance**: `pre_edit_guard.py` <= 540 LOC. `pytest -q` green.
`ruff check` clean. Manual: trigger one block per category, confirm
stderr message byte-identical.

### Commit 3 — Promote `_files_touched` to `src/git_utils.files_touched` (~1h)

**Created**: `src/git_utils.py` (~80 LOC)
**Modified**: `eval/calibration_corpus.py` (3 call sites: lines 110, 111, 124;
local `_files_touched` deleted)

API: `files_touched(repo: str, sha: str) -> set[str]` + `_run_git` helper
it depends on. Atomic single commit (no shim — `_files_touched` was private).

**Tests**: existing `tests/test_calibration_corpus*.py` keep passing.
New: `tests/test_git_utils.py` (tmp git repo fixture).

**Acceptance**: `grep -rn "_files_touched" src eval tests` returns 0 hits.
Suite green.

### Phase 0 risks (5)

1. **Glob over-capture**: `src/hooks/*.py` picks up `__init__.py`. Filter
   `_*test*.py`; document the rule.
2. **Import-time cycle**: if `_dispatch` ever imports `pre_edit_guard` for
   types -> breaks. Use stringified annotations only; CI smoke step
   `python -c "import src.hooks._dispatch"`.
3. **No shim for `_files_touched`**: leading underscore signaled private.
   Pre-flight: `grep -rn "_files_touched(" .` confirmed only 3 internal
   sites. Atomic rename safe.
4. **`main()` shape change vs test fixtures**: tests monkeypatch
   `_post_score`, `_extract_changes`, `_format_block`. Keep these names
   at module top of `pre_edit_guard.py`. Run full pytest before commit 2.
5. **CI signal post-split**: lint-and-test + eval workflows must both pass.
   Manually trigger after commit 2; confirm hook still loads in real
   Claude session via `echo '{}' | python src/hooks/pre_edit_guard.py`.

### Phase 0 out-of-scope

- No behavioral changes to any gate
- No new gates, no threshold tuning, no shadow-mode posture changes
- No move of `_format_block` / `audit_log` / `_extract_changes`
- No promotion of other private helpers beyond what `files_touched` needs
- No docs update; defer to Phase 0c if needed

---

## Phase 3.5: v3 Cross-Family Kappa Dataset (Day 2, ~6.5h)

LLM-sci flagged kin-judge contamination on v2 (devstral judge + qwen-coder
test = same coder family) as the single highest-leverage fix. Phase 3.5
ships the cross-family rebuild.

### Branch: `phase-3.5/v3-cross-family`

### Pre-flight (before any commit)

- [ ] `scw config get secret-key --profile circit` returns key
- [ ] All 3 judge models reachable via `curl https://api.scaleway.ai/v1/models`:
  - J1: `devstral-2-123b-instruct-2512` (coder family, kept from v2)
  - J2: `llama-3.3-70b-instruct` (general/Meta, NOT coder)
  - J3: `mistral-small-3.2-24b-instruct-2506` (general/Mistral, NOT coder)
- [ ] $20 budget pre-approved; cost tracker aborts at $18 soft / $20 hard
- [ ] 50-pair pilot subset = `random.Random(42).sample(load_pairs(v1), 50)`
  -> `eval/datasets/grounding_pilot50_ids.txt`
- [ ] Pre-registered independence test: pairwise Cohen's kappa on
  (J1,J2), (J1,J3), (J2,J3) over 50 pilot labels; gate `max < 0.7`

### Commit 1 — `eval/relabel_grounding_pairs_v3.py` (~150 LOC) (~2.5h)

**Created**: `eval/relabel_grounding_pairs_v3.py` (~150 LOC),
`eval/datasets/grounding_pilot50_ids.txt` (50 lines)

Fork of `eval/relabel_grounding_pairs.py:65-203`. Add:
- `--judges j1,j2,j3` CSV flag (default = pre-registered 3-judge set)
- Loop over judges; each owns its own resumable cache file
  `<out>.judge.<model_id>.jsonl` (extends pattern at line 110 of v1 script)
- `--mode {pilot,full}`: pilot reads pilot50 ids; full runs 200 + applies
  2-of-3 majority filter
- Cost meter: $/call estimate × counter; abort at $20 hard

`gen_client` re-reads env per call (verified) so per-judge `RC_GEN_MODEL`
swap works.

**Acceptance**: pilot mode produces 3 cache files × 50 entries; idempotent
on re-run.

### Commit 2 — Independence pilot run + pairwise kappa gate (~1h)

**Created**: `eval/runs/judge_independence_pilot_20260506.json`
(plus `--check-independence` subcommand in script, ~25 LOC)

Run pilot: 50 pairs × 3 judges (~5min wall, ~$3). Compute pairwise Cohen's
kappa (reuse helper from `qwen_grounding_eval.py:46-55`). Emit JSON with
`{pairwise_kappa: {j1_j2, j1_j3, j2_j3}, max, gate_pass}`.

**Acceptance**: `gate_pass: true` (max pairwise kappa < 0.7).

**Fallback chain on fail**: swap J3 -> `qwen-2.5-72b-instruct`; if still
high, swap J2 -> `gpt-oss-120b`. Document choice in pilot JSON.

### Commit 3 — Full 200-pair re-label + 2-of-3 majority filter (~1h)

**Created**: `eval/datasets/grounding_pairs_v3.jsonl` (~150-180 pairs),
`eval/datasets/grounding_pairs_v3.jsonl.summary.json`

Run `--mode full --in eval/datasets/grounding_pairs.jsonl`. Loads 3 caches
(50 already populated from pilot, idempotent), labels remaining 150 × 3.

For each pair: majority label = mode of 3 judge labels. Keep iff:
- ≥2-of-3 reachable judges agree on a non-None label, AND
- majority equals teacher label `p["label"]` (preserves v2 semantics)

Atomic write per round-2 fix at line 159-171.

**Acceptance**: `wc -l grounding_pairs_v3.jsonl` ≥ 150. Per-category
retention ≥ 60% each (pos/shuf/hard).

**Fallback if <150**: relax to "majority of reachable judges" with ≥2
reachable required.

### Commit 4 — Re-run kappa eval + COH_DELTA_EPSILON (~1.5h)

**Created**: `eval/runs/qwen_grounding_v3_20260506.json`,
`eval/runs/coh_delta_epsilon.json`,
`eval/compute_coh_delta_epsilon.py` (~40 LOC)

Run `python -m eval.qwen_grounding_eval --pairs grounding_pairs_v3.jsonl`
(no code change — `--pairs` flag exists at qwen_grounding_eval.py:143-148).

Then `compute_coh_delta_epsilon.py`: load v3 benign subset
(label==1 / pos_*), 1-sigma of `coherence_delta` -> JSON with
`{epsilon, n, source_run}`. Require `n >= 30`.

**Acceptance**: `kappa >= 0.6 AND ci95_lo >= 0.5 AND unreachable_rate <= 0.05`.
`coh_delta_epsilon.json` has `n >= 30`.

### Commit 5 — Sentinel update + flip default (~0.5h)

**Modified**: `eval/qwen_grounding_eval.py` (1-line edit at line 145:
default `--pairs` -> `grounding_pairs_v3.jsonl`),
`eval/runs/qwen_kappa_gate.json` (regenerated by commit 4 + augment with
`dataset_version: "v3", model_id, judges: [j1,j2,j3]`)

Patch sentinel writer at `qwen_grounding_eval.py:246-247` to include
new fields (additive — CDGS consumer unaffected).

**Acceptance**: re-running eval with no `--pairs` arg picks up v3.
Sentinel JSON parses with new keys.

### Phase 3.5 risks (5)

1. **Judge unreachable mid-run** -> per-judge cache makes resume trivial;
   partial 5xx -> cache `None`, retry next run.
2. **Pairwise kappa >= 0.7** (judge-family collinear) -> fallback chain
   swap J3 -> qwen-72b; if still high, J2 -> gpt-oss-120b.
3. **Cost overrun beyond $20** -> hard abort in cost meter; pilot ($3)
   gates spend before full ($10).
4. **2-of-3 yields < 150 pairs** -> fallback to "majority of reachable
   with ≥2 reachable"; second fallback: drop teacher-agreement requirement.
5. **COH_DELTA_EPSILON n < 30** -> extend benign source to v1 + v3 union;
   if still <30, defer epsilon to Phase 3.6 and gate SCR auto-apply on it.

### Phase 3.5 out-of-scope

- SCR auto-apply (Phase 3) — gates on this dataset, ships separately
- Mamba-3 cutover (Phase 4) — also gates on this dataset
- Phase 3.6 30-pair ground-truth corpus — parallel track

---

## Combined sequencing

```
Day 1 morning:   Phase 0 commit 1 (_guard_paths)        [2.5h]
Day 1 afternoon: Phase 0 commit 2 (_dispatch)           [4h]
Day 1 evening:   Phase 0 commit 3 (git_utils) + buffer  [2.5h]
Day 2 morning:   Phase 3.5 pre-flight + commits 1-2     [3.5h]
Day 2 afternoon: Phase 3.5 commits 3-4                  [2.5h]
Day 2 evening:   Phase 3.5 commit 5 + green CI verify   [0.5h]
                 Total: ~15.5h
```

Each commit lands atomically with green `pytest -q -m "not live"` + green
CI before the next starts. Push after each phase completes.

## Combined acceptance

- [ ] `pre_edit_guard.py` LOC <= 540 (Phase 0)
- [ ] `grep -rn "_files_touched" src eval tests` returns 0 (Phase 0)
- [ ] `eval/datasets/grounding_pairs_v3.jsonl` ≥ 150 pairs (Phase 3.5)
- [ ] Pairwise judge kappa < 0.7 verified (Phase 3.5)
- [ ] `qwen_kappa_gate.json` carries `dataset_version: v3` + reaches
      `kappa >= 0.6 AND ci95_lo >= 0.5` (Phase 3.5)
- [ ] All 8 commits push green to main; CI lint-and-test + eval green
- [ ] Loop-closure plan v6 tracker rows updated to mark Phase 0 + Phase 3.5
      shipped; subsequent phases (1, 2, 3, 3.6, 4) re-confirm deferred

## What this unblocks

- **Phase 0 unblocks**: Phase 1 (audit-history + viz CLIs slot into the new
  `_dispatch` chain cleanly), Phase 2 (rule engine inserts at `_dispatch`
  position 4 without touching `pre_edit_guard.main`), Phase 3 (SCR loop
  inserts at `_dispatch` post-score chain end)
- **Phase 3.5 unblocks**: SCR auto-apply (Phase 3 promotion criterion #1
  + #2), Mamba-3 cutover (Phase 4 condition c), calibration FPR promotion
  (P4 corpus producer can use v3 dataset)

## Confidence

High on Phase 0 (pure refactor, behavior-preserving). Medium-high on
Phase 3.5 (depends on judge availability + pairwise kappa < 0.7; 2 fallback
chains documented).
