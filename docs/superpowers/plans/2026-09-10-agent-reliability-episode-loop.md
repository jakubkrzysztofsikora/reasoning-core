# Agent reliability episode loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Close the edit → deterministic check → concise feedback → bounded repair → outcome loop locally, and prove the installed evidence pipeline with a real `rc doctor` canary.

**Architecture:** No new storage. Episodes are derived from existing per-session audit JSONL (`edit` decisions, `verification_recorded`, `session_outcome_recorded`) by a conservative builder that never infers exact parent links. A new PostToolUse hook runs only cheap, deterministic checks (parse, configured lint, project rules) after Edit/Write/MultiEdit, records receipts in-process, and returns a compact `additionalContext` summary. Repair is bounded to 2 attempts, then the agent must abstain and ask the user. Neural scoring is never consulted and never blocks. `rc doctor` gains a temporary, synthetic canary that executes the installed hook against an isolated audit root and then deletes it.

**Tech Stack:** Python 3.11, stdlib only for new code (subprocess for ruff, existing `audit_log`, `_oracles`, `_rule_engine` reuse), pytest, git.

**Spec:** operator research synthesis 2026-09-10 (this conversation); context in `thoughts/shared/research/2026-09-08-value-promise-and-evaluation-improvements.md` and `thoughts/shared/handoffs/2026-09-09-real-session-evidence-pipeline.md`.

## Global Constraints

- Session-level checks must stay distinct from exact decision evidence. Never infer `parent_decision_id` from recency.
- The post-edit hook must never block, never exit non-zero, and never read neural scores.
- Repair budget: max 2 attempts, then abstain ("ask the user").
- Canary artifacts are synthetic: isolated `RC_AUDIT_ROOT`, `rc-doctor-canary-` prefix, deleted in `finally`, and excluded by `_is_synthetic_fixture`.
- No commits unless the operator asks.

## File structure

- Create `src/hooks/_episodes.py` — episode builder, recovery policy, markdown render.
- Create `src/hooks/post_edit_check.py` — PostToolUse fast-check hook + feedback.
- Create `src/hooks/_canary.py` — doctor evidence canary (isolated + cleanup).
- Modify `src/hooks/_session_correlator.py` — exclude `rc-doctor-canary-` fixtures.
- Modify `src/rc_cli.py` — `rc episodes`, `--file-path` on record-verification, doctor canary check.
- Modify `.claude/settings.json`, `install.sh` — wire post-edit hook.
- Modify `docs/USAGE.md`, `README.md` — document loop.
- Tests: `tests/test_episodes.py`, `tests/test_post_edit_check.py`, `tests/test_canary.py`, additions to `tests/test_session_correlator.py`.

## Tasks

### Task 1: Episode builder + recovery policy (`_episodes.py`)

Interfaces:
- `SCHEMA_VERSION = 1`, `MAX_REPAIR_ATTEMPTS = 2`
- `build_episodes(audit_root, *, days=30, session_id=None, project_dir=None, include_synthetic=False) -> list[dict]`
- `recovery_policy(failure_count: int) -> dict` → `{"status": "ok"|"repair"|"abstain", "attempt": int, "max_attempts": 2, "next_action": str}`
- `repair_state(episode: dict) -> dict` → delegates to `recovery_policy` using failed checks and repair edits.
- `render_markdown(episodes: list[dict]) -> str`

Episode fields: `schema_version, episode_id, session_id, task_id, project_dir, root_decision_id, file_path, file_path_rel, started_ts, ended_ts, edit_events[], checks[] (association + exact), repair_attempts[], final_status, session_outcome`.

Grouping: per `(project_dir, session_id)`, then per `(task_id or "", file identity)`. Explicit checks link by `parent_decision_id`; session-level checks attach by same file identity and are marked `exact: False`. Unattributed checks are dropped, never guessed.

- [x] RED: `tests/test_episodes.py` — explicit chain, session-level association flagged non-exact, repair count, budget states 1/2/3, synthetic exclusion.
- [x] GREEN: minimal `_episodes.py`.
- [x] Run `pytest tests/test_episodes.py -q` green.

### Task 2: `rc episodes` CLI + `--file-path` record field

- [x] RED: `tests/test_rc_cli_episodes.py` — `rc episodes --json` from fixture audit root returns episode with file/check/recovery keys; `record-verification --file-path` persists `file_path_rel`.
- [x] GREEN: `cmd_episodes`, argparse, `--file-path` pass-through.
- [x] Run tests green.

### Task 3: PostToolUse fast-check hook (`post_edit_check.py`)

Checks: parse (`.py` compile, `.json`, `.toml`, `.yaml`), lint (ruff via `_oracles._ruff_command()`, `.py` only), project rules (`_rule_engine.load_rules` + `evaluate_edit`, first hit). Skip files >1 MB and outside project dir. `RC_POST_EDIT_CHECKS=0` disables. Per-check timeout 5 s. Record receipts in-process via `audit_log.append_correlated_event` with `file_path`, `association_type` explicit only when the host supplies a parent ID. Feedback on failure only, as `hookSpecificOutput.additionalContext` JSON; always exit 0.

Feedback format:

```text
Verification: FAILED
Check: ruff
File: src/foo.py
Files: 1
First actionable error: F401 unused import ...
Repair: attempt 1 of 2 — fix and rerun.
Next action: repair, then rerun.
```

Budget exhausted replaces repair line with `Next action: stop and ask the user; do not attempt further repairs.`

- [x] RED: `tests/test_post_edit_check.py` — parse pass/fail, json fail, feedback text shape, abstain at 3rd failure, valid receipt schema (kind/status/association/file), non-edit tool no-op, disabled env no-op.
- [x] GREEN: hook implementation.
- [x] Run tests green.

### Task 4: Doctor canary (`_canary.py` + `cmd_doctor`)

- [x] RED: `tests/test_canary.py` — canary produces ≥1 `verification_recorded` receipt in isolated root with `correlation_schema_version`, `session_id` prefix, `project_dir` match; temp dir removed; `_is_synthetic_fixture` recognizes prefix; doctor JSON exposes `evidence_canary` with `synthetic` marker.
- [x] GREEN: `_canary.run_evidence_canary()`, doctor check + static wiring checks for `post_edit_check.py`.
- [x] Run tests green.

### Task 5: Wiring + docs

- [x] Add PostToolUse Edit|Write|MultiEdit entry to `.claude/settings.json`.
- [x] Mirror entry in `install.sh` template.
- [x] `docs/USAGE.md` + `README.md` command rows and loop description.
- [x] Run `rc doctor` manually; confirm canary pass and no residue.

### Task 6: Verification

- [x] `pytest -q -m "not live"` for touched suites, then full suite if fast.
- [x] `ruff check` on changed files (if available).
- [x] `rc episodes` on the real local audit root (read-only) prints episodes.
- [x] Adversarial review via `~/.config/adversarial-reviewer/review.sh`; fix BLOCKER/MAJOR.
