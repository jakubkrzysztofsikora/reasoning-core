# Dup advisory: boundary findings, move filter, decision signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reuse-check findings land when the agent is receptive and in a form it acts on, and stop false-alarming on code relocations.

## Problem

`src/hooks/pre_edit_dup_advisory.py` fires PreToolUse, mid-increment, and ends with
"advisory only -- reuse or extend if it fits, otherwise proceed." Two failure modes:

1. **Skipped under momentum.** Genuine multi-site duplications (e.g. the same
   click-outside handler in 3 components) are waved through and never come back;
   the hook is stateless, so a skipped finding evaporates.
2. **False alarms on moves.** When a function is moved from file A to new file B,
   the Write to B sees A's copy (not yet deleted) and reports `logic 1.00`. The end
   state has one copy. False alarms train the agent to discount the tool.

## Context (current code)

- Hook: `pre_edit_dup_advisory.py` — `advise()` (pure core, `:90-137`), `main()`
  (`:181-207`), opt-in `RC_DUP_ORACLE=1`, fail-open, never blocks. Not wired in
  `.claude/settings.json` / `install.sh`; documented in `docs/dup-oracle.md`.
- Matching: `src/dup_oracle.py` (cos ≥ 0.80 shortlist → logic-token ratio ≥ 0.97
  confirm → rank by distinctive shared tokens). `TOP_K = 3`.
- No findings persistence; no knowledge of the current diff.
- Stop-hook precedent: `src/hooks/stop_reconcile.py` (diffs the working tree,
  honours `stop_hook_active`).
- Tests: `tests/test_pre_edit_dup_advisory.py` (stub embedder, `tmp_path` `.ts`
  repos, `advise()` direct + `main()` via monkeypatched stdin).

## Behaviours (Given / When / Then — from the ticket)

- **B1 Boundary surfacing.** Given a non-trivial duplication spanning several
  sites, when the check runs, then it is surfaced as an actionable item at a
  natural boundary (Stop), not only inline at write time.
- **B2 Relocation suppressed.** Given a function being moved from a file in the
  working diff into another file, when the check runs, then no duplicate is
  reported (inline it is downweighted to a one-line "likely move" note; at the
  boundary it is dropped if the source copy is gone).
- **B3 Resurface.** Given a finding that was not acted on, when the session
  reaches the Stop boundary, then it resurfaces instead of evaporating.
- **B4 Decision signal.** Given a surfaced finding, it states the number of sites
  and whether the sites are test-covered.
- **B5 Routing.** Given a finding whose matches all sit outside the current diff,
  it is routed as a tracked follow-up, not an inline "refactor these unrelated
  files" nudge.

## Design (proposed — reshape at the checkpoint)

1. **Findings ledger** — new `src/hooks/_dup_findings.py`. Per-repo, per-session
   JSONL under the existing cache root
   (`~/.cache/reasoning-core/dup-findings/<repo-hash>/<session_id>.jsonl`, or
   `$RC_CACHE_DIR`). Each row: added fn (path, name), match sites (path, line,
   name, logic), `likely_move`, `in_diff`, `status` (open / surfaced / resolved).
   Outside the repo tree, so nothing lands in Jakub's git status.
2. **Diff awareness** — `changed_files(repo_root)` via `git status --porcelain`
   (read-only, allowed by `pre_bash_guard`; runs inside the hook process, not the
   agent's Bash). Fails open to "unknown" with a stderr note — never silent.
3. **Move filter** — a hit is `likely_move` when its source file is modified in
   the working tree (or is the file being edited) and logic ratio ≥ confirm.
   Inline output collapses it to one line; the boundary re-checks whether the
   source function still exists and drops the finding if it is gone.
4. **Sites + coverage** — sites = 1 (added) + confirmed matches (raise per-fn
   `TOP_K` cap for counting only, display still capped). Test-covered = a test
   file (`tests?/`, `*.test.*`, `*_test.*`, `test_*`) references the function
   name — a cheap, stated heuristic, labelled as such in the output.
5. **Rule framing** — replace "advisory only… proceed" with a mechanical rule:
   `sites ≥ 3 AND non-trivial (≥ N logic tokens) AND test-covered → EXTRACT`;
   otherwise "note only". In-diff overlap → inline "extract now"; all-outside-diff
   → "tracked follow-up" (recorded in ledger, surfaced at Stop).
6. **Stop hook** — new `src/hooks/stop_dup_findings.py`: reads the session
   ledger, re-validates (both copies still present? still a match?), emits one
   compact batch: EXTRACT items first, follow-ups, note-only count.
   - **EXTRACT-rule findings block the stop** until each is either extracted
     (re-validation no longer finds the duplicate) or deferred with a reason
     via `rc dup-defer <finding-id> --reason "..."` (ledger row → `deferred`).
     A warning is what the agent already ignores; the block is the point.
   - Allowed by AGENTS.md: the cosine shortlist is neural, but every finding is
     confirmed by the deterministic logic-token ratio (difflib, ≥ 0.97), and the
     EXTRACT rule's inputs (site count, size, test reference) are deterministic.
   - Note-only findings never block. `stop_hook_active` → approve (no loop);
     open rows resurface at the next Stop. `RC_MODE=advise` → never blocks.
   - Rows are marked `surfaced` so note-only items show once per boundary.

## Safe-by-default

- Whole feature stays **opt-in** (`RC_DUP_ORACLE=1`). The PreToolUse hook still
  never blocks. The Stop block is a new enforcement default for opted-in repos,
  so per AGENTS.md an immutable prechange baseline
  (`baseline-2026-10-08-dup-advisory-prechange`) is captured before it lands.
  No threshold (0.80 / 0.97) changes. Blocks only on the deterministic EXTRACT
  rule; `RC_MODE=advise` and `stop_hook_active` always approve.
- Every degrade (git unavailable, ledger write fails) is observable: stderr
  note + a status field, never a bare swallow.

## Reality recon

- Known-codebase work; the external boundary is the Claude Code hook protocol
  (Stop payload + `additionalContext`), already used by `stop_reconcile.py`.
  Before Increment 6, capture one real Stop payload from a session to confirm
  the fields read (`session_id`, `cwd`, `stop_hook_active`).

## Increments (test-first; each a commit)

- [ ] **1. Ledger** (`_dup_findings.py`): append / read / mark-surfaced round-trip
  in `tmp_path`; write failure is reported, not swallowed. → B3
- [ ] **2. Diff awareness**: `changed_files()` on a `tmp_path` git repo (clean,
  modified, untracked); non-git dir → explicit "unknown". → B2, B5
- [ ] **3. Move filter in `advise()`**: regression case A (`initialsOf` moved
  out of a modified `Avatar.ts` into new `initials.ts`) → no full finding, at
  most a "likely move" line. Unmodified-source genuine dup still reported. → B2
- [ ] **4. Sites + coverage + rule framing**: regression `onDocClick` (3 sites,
  non-trivial, a test references it) → `EXTRACT` with "3 sites, test-covered";
  `setUrl`/`renderApp` (trivial) → note-only; old "otherwise proceed" text
  gone. → B4
- [ ] **5. Routing + ledger write from `main()`**: all-outside-diff →
  "tracked follow-up" wording + ledger row; in-diff → inline extract. → B1, B5
- [ ] **6a. Baseline**: capture `baseline-2026-10-08-dup-advisory-prechange`,
  commit manifest + README row.
- [ ] **6b. `rc dup-defer`**: marks a ledger row deferred with a required
  non-empty reason; unknown id → error. → B3, B5
- [ ] **6c. Stop hook**: open EXTRACT row → block with batched message; deferred
  or extracted (dup gone) → approve; move source deleted → dropped; note-only →
  shown, never blocks; `stop_hook_active` / `RC_MODE=advise` / empty ledger →
  approve. → B1, B2, B3
- [ ] **7. Docs**: `docs/dup-oracle.md` — wiring for the Stop hook, the rule,
  the move heuristic and its limits.

## Out of scope / risks

- Not wiring the hooks into `.claude/settings.json` / `install.sh` by default
  (opt-in stays; `settings.json` is a guarded file anyway).
- No ship-time (`/ship`) integration — that's personal tooling, not this repo.
- Test-coverage signal is a name-reference heuristic, not real coverage;
  labelled as such.
- Move detection is a heuristic: a genuine copy into a file that also happens to
  be modified gets downweighted inline, but the Stop re-check (source still
  present ⇒ real dup) catches it.
