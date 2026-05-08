---
date: 2026-05-06
commit: 6a921ce
branch: main
ticket: readme-content-refresh
status: draft
informed_by:
  - 2-subagent analysis (impl snapshot + README structure audit, 2026-05-06)
  - thoughts/shared/plans/2026-05-06-iter2-100pct-eval-plan.md
  - thoughts/shared/plans/2026-05-06-system-2-loop-closure.md (v6)
---
# Plan: README content refresh (structure preserved)

## Summary

The README is 893 lines, last touched at `3053ce3` (2026-05-06 morning).
Subsequent commits (`80b5543`, `4ed3245`, `5313498`, `b7f0517`, `6a921ce`,
`c452cb4`, `c0118a4`, `4997ec7`, `dcd3598`, `1738b57`, `1bc2718`) shipped
P5 round-3, grounding_pairs_v2, supervisor recalibrate watcher, GUARDED_PATHS
expansion, sign-test, and CI eval-workflow stabilization.

Goal: refresh content to match HEAD reality. Structure (23 sections) stays
intact; 16 sections are clean, 7 need targeted edits.

## Sections to update (in document order)

### §1: Header / badges (lines 1-12)
**Add**: CI badges for `lint-and-test` and `eval` workflows. Both green
since `c452cb4`. Suggested:
```markdown
[![lint-and-test](https://github.com/.../actions/workflows/lint-and-test.yml/badge.svg)](...)
[![eval](https://github.com/.../actions/workflows/eval.yml/badge.svg)](...)
```

### §6: What you get out of the box (lines 188-247)
**Stale**: claim "Cohen κ ≥ `RC_QWEN_KAPPA_SENTINEL` (default 0.7)" frames
the gate as a hard pass.

**Update**: add a sentence acknowledging the live κ run:
> Live κ measured on the v2 grounding dataset (`eval/datasets/grounding_pairs_v2.jsonl`,
> 138 judge-relabeled pairs) is **0.74**. Note: this κ is contaminated by
> kin-judge family (relabeling judge and held-out test model are both
> coder-LLMs); the gate runs **advisory** until a v3 cross-family dataset
> is built (Phase 3.5 of `2026-05-06-system-2-loop-closure.md`).

### §7: Run it locally (lines 250-315)
**Gap**: Scaleway-backend path for non-Apple users (Linux/CI) buried in
later "Generative repair head" section.

**Update**: between current step 4 and 5 add an optional step:
```markdown
4b. (Optional, non-Apple silicon) Configure Scaleway-hosted generative critic:

    bash scripts/configure-scaleway.sh
    # Sets RC_REASONER_BACKEND=remote, RC_GEN_API_KEY from `scw config`
```
Apple-silicon users keep the local `mlx_lm.server` path (current default).

### §13: Evaluation harness (lines 435-456)
**Stale**: references only `eval/datasets/grounding_pairs.jsonl` (v1).

**Update**: add a one-line note:
> Two grounding datasets ship: v1 (`grounding_pairs.jsonl`, 200 pairs,
> git-mined) and v2 (`grounding_pairs_v2.jsonl`, 138 pairs, devstral-123b
> judge-relabeled high-confidence subset). Default for κ eval is now v2
> per `qwen_grounding_eval.py` defaults; v1 retained for reproducibility.

Also reference the new `eval/relabel_grounding_pairs.py` script
(`4ed3245`) under "How to rebuild the dataset".

### §14: Benchmarks — iteration 1 (lines 459-538)
**Stale framing**: "Iteration 2 (in progress) ships the iter-1 failure-mode
fixes (mock-detector, plan-quality CGS, language-fingerprint lock)".

**Update**: rewrite the framing sentence to:
> Iteration 2: failure-mode fixes shipped (mock-detector P1, plan-quality
> CGS P2, language-fingerprint lock P3, calibration concurrent with shadow
> P7); re-run scheduled. Sign-test wired in `eval/stats.py::sign_test`
> (`6a921ce`); pre-registered acceptance ≥7/8 task-mean wins for Setup B
> with ≥1.0 BARS gap and sign-test p ≤ 0.05.

Numbers in the table itself stay (they are iter-1 ground truth).

### §16: Configuration (lines 577-652)
**Stale**: `RC_QWEN_KAPPA_SENTINEL` default-0.7 framing.

**Update**: align with §6 — add a one-line caveat that the 0.7 sentinel is
a target gate, current live measurement is 0.74 contaminated, gate is
advisory until v3 cross-family dataset.

Also add:
- `RC_DRIFT_WARN` / `RC_DRIFT_DENY` defaults (4.0 / 6.0) — clarify these
  are **placeholder cumulative_drift constants** pending Phase 3.5
  calibration; not production-tuned.
- `RC_GEN_API_KEY` / `SCALEWAY_API_KEY` — Bearer auth for hosted critic
  (added in `80b5543`).
- `RC_RECALIBRATE_POLL_S` — supervisor watcher poll interval (default 60s,
  added in `5313498`).
- `RC_CALIBRATION_ENABLED` — Mahalanobis gate (default off, added in
  `b7f0517`).

### §18: Project layout (lines 674-759)
**Stale**: missing post-3053ce3 modules.

**Update**: add to `src/`:
- `_supervisor_recalibrate.py` — watcher consuming `recalibrate.signal`
  (`5313498`)
- `_calibration_gate.py` — hot-path Mahalanobis gate (`b7f0517`)

Add to `tests/`:
- `test_supervisor_recalibrate.py`
- `test_calibration_integration.py`
- `test_sign_test.py` (`6a921ce`)
- `test_gen_client_round2.py` (verdict parser tests)

Add to `eval/`:
- `relabel_grounding_pairs.py` — judge-relabel pipeline (`4ed3245`)
- `datasets/grounding_pairs_v2.jsonl` — v2 dataset
- `runs/grounding_*_20260506.json` — live κ results

### §22: Roadmap (lines 841-881)
**Stale "Shipped" list**: ends at P-1/P0/P1/P2/P3/P4/P5/P7 but predates
recent rounds.

**Update — add bullets**:
- ✅ P5 round-3 hardening (4-reviewer findings closed; `b7f0517`)
- ✅ Grounding pairs v2 (judge-relabeled, κ=0.74 on v2; `4ed3245`)
- ✅ Supervisor consumes `recalibrate.signal` for hot-refit (`5313498`)
- ✅ Iter-2 readiness blockers closed: GUARDED_PATHS expansion,
  binomial sign-test in eval/stats.py (`6a921ce`)
- ✅ CI eval workflow stabilized (sharded safetensors fallback, lazy
  prefetch, run-id arg, contents:write permission;
  `c452cb4`/`c0118a4`/`4997ec7`/`dcd3598`/`1738b57`/`1bc2718`)

**Update "Open" list**:
- Drop or rephrase "Subagent loop path + LLM-judge gate behind
  `RC_COHERENCE_LLM=1` (deferred from P5)" — Scaleway-hosted judge IS
  live (`80b5543`); the open piece is now the SCR loop (Phase 3 of the
  system-2-loop-closure plan).
- Add: "v3 cross-family κ dataset (Phase 3.5 of system-2-loop-closure
  plan) — 200 pairs × 3 judges (devstral / llama-3.3 / mistral-small)
  with pairwise-κ < 0.7 independence test."
- Add: "rc audit-history + rc viz Mermaid dashboard + npx reasoning-core
  init one-line installer (Phase 1 of system-2-loop-closure plan)."

## Sections that are CLEAN (no edit)

§2 TL;DR · §3 ToC · §4 Why · §5 System 1+2 · §8 Under the hood ·
§9 Hook layers · §10 CLI · §11 Supervisor & launchd · §12 Shadow mode ·
§15 Scoring math · §17 Usage from code · §19 FAQ · §20 Testing ·
§21 Contributing · §23 Acknowledgements + License

## Edit strategy

1. **Single PR, atomic commit**: one commit per section, with the section
   number in the commit subject (`docs(README §14): align iter-2 framing
   with shipped phases`).
2. **No structural changes**: section numbers, ToC anchors, h2/h3 levels
   stay identical.
3. **Defer `2026-05-06-readme-100pct-current-state.md`**: that plan was
   for a 100% rewrite; this refresh keeps existing structure. Keep the
   file as historical context; don't ship.

## Calibration math labelling fix

The implementation snapshot agent flagged: README/docs may still say
"James-Stein per-kind shrinkage" but the code is renamed
`_empirical_bayes_shrink` (conjugate posterior — round-2 reviewer fix per
LLM-sci correction). If §15 or §22 mentions James-Stein, update to
"empirical-Bayes per-kind shrinkage (conjugate posterior with α=5 anchor)".

Verification: grep README for "James-Stein"; if hits, update inline.

## Setup B description fix (§14)

Subagent flagged: "Setup B shadow-mode off" framing in the benchmarks
section is misleading. Setup B was a *measurement configuration* (not
the default operator posture). Default `.envrc` ships shadow-mode-on;
Setup B as evaluated had specific overrides. Add a 1-line clarifier so
readers don't think shadow-mode-off is recommended.

## Quickstart parity check (no change needed)

Subagent confirmed: 6-step quickstart works for Apple Silicon. Only gap
is the optional Scaleway step, addressed in §7 update above. No other
changes required.

## Risk / rollback

- Single large doc commit risk: mitigated by per-section commits.
- Inflight v6 system-2-loop-closure plan references content the README
  now points back to (`Phase 3.5 cross-family dataset`, etc.) — these are
  internal cross-references, won't break if reader doesn't follow them.
- CI badges: ensure the badge URLs match the actual workflow names
  exactly (`lint-and-test.yml` and `eval.yml` — verified).

## Success criteria

- [ ] All 7 sections flagged as stale have content updated to match HEAD
- [ ] CI badges added (§1)
- [ ] No structural changes (line-count delta < +50 lines)
- [ ] No section renames or h-level changes
- [ ] grep verification: no remaining references to "James-Stein" /
  "iter-2 in progress" / "default 0.7 hard gate" wording
- [ ] PR review: 1 approval before merge
- [ ] Post-merge: `pytest -q -m "not live"` still 287 pass (docs-only PR,
  shouldn't change tests)

## Estimated effort

~2 hours. 7 section edits, mostly mechanical. CI badge addition is the
only step that touches the file-top.

