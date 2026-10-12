# Dup advisory: boundary findings, move filter, decision signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reuse-check findings land when the agent is receptive and in a form it acts on, and stop false-alarming on code relocations.

## Problem

The per-edit duplicate check fired PreToolUse, mid-increment, and ended with
"advisory only -- reuse or extend if it fits, otherwise proceed." Two failure modes:

1. **Skipped under momentum.** Genuine multi-site duplications (e.g. the same
   click-outside handler in 3 components) are waved through and never come back;
   the hook is stateless, so a skipped finding evaporates.
2. **False alarms on moves.** When a function is moved from file A to new file B,
   the write to B sees A's copy (not yet deleted) and reports `logic 1.00`. The end
   state has one copy. False alarms train the agent to discount the tool.

## Context (current code)

- Engine package `src/duplicate_detection/`: `index.py` (normaliser, logic
  tokens, ranking), `oracle.py` (cos ≥ 0.80 shortlist → logic ratio ≥ 0.97 confirm
  → distinctiveness rank, `TOP_K = 3`), `repo_index.py` (disk-cached index),
  `embed.py`, `findings.py` (per-repo ledger + `changed_files()`, done).
  Not guarded — advisory engine, bound by tests.
- Hook `src/hooks/post_edit_duplicate_check.py` (renamed from the pre-edit
  version; still PreToolUse code until switched in Increment 5). Guarded:
  hook edits are applied by the operator.
- Separate deterministic hard block: `pre_edit_guard` →
  `project_index.find_duplicate_definitions` (Python, same name or identical
  body). May hard-block a Python function move; verified as a regression case.
- Stop-hook precedent: `src/hooks/stop_reconcile.py` (`stop_hook_active`).

## Behaviours (Given / When / Then)

- **B1 Boundary surfacing.** A non-trivial duplication spanning several sites is
  surfaced as an actionable item at Stop, not only inline at write time.
- **B2 Relocation suppressed.** A function moved from a file in the working diff
  into another file produces no finding (inline: silent, recorded as likely move;
  at Stop: dropped once the source copy is gone).
- **B3 Resurface.** A finding not acted on resurfaces at Stop.
- **B4 Decision signal.** A surfaced finding states site count and whether the
  sites are test-covered.
- **B5 Routing.** In-diff overlap → inline nudge after the edit. Matches all
  outside the diff → recorded as a tracked follow-up, no inline nudge.

## Design

1. **Ledger** (`findings.py`, done): per-repo JSONL under the cache root, outside
   the repo tree; statuses open / surfaced / deferred / resolved.
2. **Diff awareness** (`changed_files()`, done): `git status --porcelain`; unknown
   → `None` + stderr note.
3. **Move filter** (`duplicate_detection`): a match is `likely_move` when its file
   is in the working diff and logic ratio ≥ confirm. Not shown inline; Stop
   re-checks the source and drops the finding when it is gone.
4. **Sites + coverage**: sites = 1 + confirmed matches (uncapped for counting).
   Test-covered = a test file references the function name (stated heuristic).
5. **Rule**: `sites ≥ 3 AND non-trivial (≥ N logic tokens) AND test-covered →
   EXTRACT`, else note-only. Replaces "advisory… proceed".
6. **Per-edit hook (PostToolUse)**: thin wrapper. Reads the edited file as it now
   is, calls the engine, records every finding in the ledger, emits inline only
   for in-diff, non-move findings. Never blocks.
7. **Stop hook** `src/hooks/stop_duplicate_check.py` (new, guarded once created):
   re-validates open rows, emits one batch (EXTRACT first, follow-ups, note-only
   count). **EXTRACT rows block** until extracted (re-validation finds no dup) or
   deferred via `rc dup-defer <id> --reason "..."`. Deterministic inputs only
   (difflib confirm, counts, test reference) → allowed by AGENTS.md.
   `stop_hook_active` and `RC_MODE=advise` always approve.

## Decisions (confirmed with the operator 2026-10-12; flagged in the PR)

1. **Block mode:** the Stop block applies whenever `RC_DUP_ORACLE=1`, not only
   in `RC_MODE=copilot` (unlike `stop_reconcile.py`; `RC_MODE` defaults to
   `advise`, which would bring the skip problem back). An explicit
   `RC_MODE=advise` disables blocking.
2. **Persistence:** keep blocking an open EXTRACT finding even when
   `stop_hook_active` is set, capped at 3 blocks per finding; then approve and
   write an `escalated` audit event. `rc dup-defer` is always an exit.
3. **Notes / follow-ups:** never force a turn; they ride along in an EXTRACT
   block, otherwise ledger + audit log only.
4. **Re-validation** at Stop is deterministic (tree-sitter + logic ratio, no
   model): finding resolved when the added copy or all matched copies are gone.

## Safe-by-default

- Feature stays opt-in (`RC_DUP_ORACLE=1`). Per-edit hook never blocks. The Stop
  block is a new enforcement default for opted-in repos → immutable prechange
  baseline `baseline-2026-10-08-dup-advisory-prechange` captured first. No
  threshold (0.80 / 0.97) changes.
- Every degrade (git unknown, ledger write fails) is observable: stderr + status.

## Regression cases (from the real session)

- **Moves → no finding:** `initialsOf` (Avatar → new `initials.ts`),
  `clearConnectParam` (ConnectStrava → `useConnect.ts`).
- **Real dup → EXTRACT, tracked follow-up:** `onDocClick` in 3 menus, matches
  outside the diff, test-covered.
- **Trivial → note-only, never blocks:** `setUrl` (3-line helper), `renderApp`.
- **Python move vs structural hard block:** confirm whether `pre_edit_guard`
  blocks a Python function move; document the result.

## Increments (test-first; each a commit)

- [x] **1. Ledger** (`findings.py`). → B3
- [x] **2. Diff awareness** (`changed_files()`). → B2, B5
- [x] **3. Move filter** in the engine: `initialsOf` / `clearConnectParam` →
  `likely_move`; unmodified-source dup → real. → B2
- [x] **4. Sites + coverage + rule** (N = 12 logic tokens, measured: click-outside
  + Escape = 23, trivial helpers 4–8; coverage = a test names the function or
  the site file's stem): `onDocClick` → EXTRACT "3 sites,
  test-covered"; `setUrl` / `renderApp` → note-only; no "otherwise proceed". → B4
- [x] **5. Routing + ledger write**: engine `check_edit()` returns inline text +
  findings; in-diff (same file) → inline once, other changed file → likely move,
  untouched files → ledger only.
- [ ] **5b. Hook switch** (operator): Operator switches the hook
  to PostToolUse and to call `check_edit()`. → B1, B5
- [ ] **6a. Baseline** capture + README row.
- [ ] **6b. `rc dup-defer`** (rc_cli.py is guarded → operator applies). → B3
- [ ] **6c. Stop hook** `stop_duplicate_check.py`. → B1, B2, B3
- [ ] **7. Docs**: `docs/dup-oracle.md` — PostToolUse + Stop wiring, rule, move
  heuristic and limits, structural-block finding.

## Out of scope / risks

- Not wiring hooks into `.claude/settings.json` / `install.sh` by default.
- Test-coverage signal is a name-reference heuristic, labelled as such.
- A genuine copy into a modified file is treated as a likely move inline; the
  Stop re-check (source still present ⇒ real dup) catches it.
