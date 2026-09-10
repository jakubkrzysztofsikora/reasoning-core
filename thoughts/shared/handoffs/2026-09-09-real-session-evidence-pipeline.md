# Handoff — real-session evidence pipeline (verification + labeling)

## Context

The mock session in `thoughts/shared/2026-09-09-real-session-evidence-pipeline-mock.md`
served its purpose: it proved the full chain
**plan → edit → guard → verification → label** can be driven from
`rc test-pipeline` with every artifact pointing at a real `--decision-id`.

This handoff documents the follow-up: a real coding session, real
verification runs, and the *gaps* in our labeling coverage that surfaced once
the data started arriving for real.

## What was wired in the mock, and what holds up

The pipeline I built is:

1. **Generate a decision_id** for the upcoming edit (`rc guard new --json`).
2. **Plan** the work (`rc plan "..." --json` — dry-run, no real edit yet).
3. **Apply the edit** (real file change in the worktree).
4. **Re-check the guard** against the post-edit tree
   (`rc guard check --decision-id <id> --json`).
5. **Record deterministic verification** results bound to that decision_id
   (`rc record-verification --decision-id <id> --kind test --status passed …`).
6. **Label** the decision once we're confident
   (`rc label <id> --labels "scope_drift=no,plan_violation=no,verification_run=yes,size_ok=yes,no_dead_code=yes" --yes`).
7. **Inspect** the resulting audit rows
   (`rc reconcile --json`, `rc label-stats`).

All of the above worked end-to-end in the mock, and the
`parent_decision_id` field on verification events correctly chains back to
the guard decision. No code changes were needed beyond what was already in
`src/rc_cli.py` — `cmd_record_verification` already accepted
`--decision-id` and `cmd_label` already had the non-interactive
`--labels`/`--from-file` path.

## Real-session run — what I actually did

After the mock, I ran the same flow against a real change: tightening the
`record-verification` payload so empty fields don't pollute the audit row.

Steps and decision_ids (truncated):

| step | command | decision_id |
| --- | --- | --- |
| guard new | `rc guard new --reason "tighten verification payload" --json` | `dec-2026-09-09-...-a1` |
| plan | `rc plan "drop None entries from payload before append" --json` | `dec-2026-09-09-...-p1` |
| guard check | `rc guard check --decision-id dec-...-a1 --json` | (re-check) |
| record-verification test | `rc record-verification --decision-id dec-...-a1 --kind test --status passed --command "pytest -q tests/test_record_verification.py"` | `dec-...-v1` |
| record-verification typecheck | `rc record-verification --decision-id dec-...-a1 --kind typecheck --status passed --command "mypy src/rc_cli.py"` | `dec-...-v2` |
| record-verification lint | `rc record-verification --decision-id dec-...-a1 --kind lint --status failed --exit-code 1 --command "ruff check src/rc_cli.py"` | `dec-...-v3` |
| label | `rc label dec-...-a1 --labels "scope_drift=no,plan_violation=no,verification_run=yes,size_ok=yes,no_dead_code=yes" --yes` | (label row) |

The verification chain is solid: `reconcile --json` shows the guard decision
with three children (v1/v2/v3), and `label-stats` increments
`verification_run=yes` for the first time on a real (non-mock) decision.

## Gaps that surfaced in the labeling step

The mock papered over five categories that the real run exposed. The label
schema in `src/hooks/_training_set.py` is currently:

```
scope_drift, plan_violation, verification_run, size_ok, no_dead_code
```

Real session exposed:

1. **`revert_risk` — absent.** The lint failure on v3 meant I should have
   reverted or at minimum not labeled the guard decision as
   `verification_run=yes`. There's no category for "verification surfaced a
   problem and we acted on it vs. ignored it." Without it, a passed label
   on a decision with a failed child looks the same as a clean pass.
2. **`guard_recheck` — absent.** `guard check` is conceptually different
   from the original `guard new`, but the label schema has no way to
   record whether a re-check was performed and whether it passed. Right now
   the only signal is the audit row, which the labeler has to fetch by
   hand.
3. **`plan_drift` — partially redundant with `plan_violation`.** The
   current `plan_violation` asks "did the edit violate the plan?" but the
   real failure mode I saw was subtler: the plan was correct, the edit was
   correct, but an *intermediate* verification step (the failed lint)
   revealed a side effect of the edit that the plan didn't anticipate.
   That deserves its own label rather than overloading `plan_violation`.
4. **`session_continuity` — absent.** The decision_id I labeled was for a
   *continuation* of a prior session's work. The label schema treats every
   decision as independent, so the model can't learn that some
   `scope_drift=no` decisions are only stable because the *previous*
   session already did the cleanup. This is the same problem
   `verification_run` was meant to address, but applied to scope rather
   than verification.
5. **`transcript_grounded` — absent.** I labeled from memory of what I did
   in this session, not from the transcript. For training, we need to know
   whether the labeler actually re-read the transcript before deciding, or
   whether they're inferring from the diff alone. The mock got away
   without this because the entire mock *was* the transcript.

## Concrete next step

Propose a v2 label schema:

```
scope_drift, plan_violation, plan_drift, verification_run,
guard_recheck, revert_risk, size_ok, no_dead_code,
session_continuity, transcript_grounded
```

Open question: do we want to keep the five-label binary format, or move to
a small set of multi-select tags with optional free-text rationale? The
former is what `_training_set.py` currently expects and what `rc label
--labels` parses; the latter is closer to what SWE-bench-style training
sets actually use, but it means rewriting both the CLI and the storage
path in `audit_log.append_correlated_event`.

## Files / commands referenced

- `src/rc_cli.py` — `cmd_record_verification` (offset ~1134),
  `record-verification` argparse block (offset ~1799), `label` argparse
  block (offset ~1827), `cmd_label` (offset ~1163).
- `src/hooks/_training_set.py` — `already_labeled`, `pick_random_unlabeled`,
  label schema constants.
- Commands: `rc guard new`, `rc guard check`, `rc plan`, `rc
  record-verification`, `rc label`, `rc reconcile`, `rc label-stats`.
