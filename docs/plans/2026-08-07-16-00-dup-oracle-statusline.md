# dup oracle statusline

- Date: 2026-08-07 16:00
- Branch: feat/dup-oracle-statusline (off feat/dup-oracle, PR #13 — do not touch that branch)

## Problem / Context

The advisory dup-oracle hook (`src/hooks/pre_edit_dup_advisory.py`, opt-in via
`RC_DUP_ORACLE=1`) warns the agent when it's about to write a function the repo
already has. There's no visibility into how often it fires. This feature adds a
Claude Code statusline segment showing a per-session count of reuse-flags the
hook raised, colour-coded green→red so a developer running the oracle can see at
a glance whether the agent keeps reinventing existing code (rising count = the
agent may be going off the rails).

Local-only, opt-in, per-session, Claude-only. NOT in scope: cross-user
telemetry / data collection.

## Plan

A rough uncommitted draft already exists (reviewed, judged worth keeping with
rework). Two programs share a per-session on-disk counter file:
- writer: the PreToolUse hook bumps the tally when it actually emits an advisory.
- reader: the statusline command reads the tally every prompt and renders it.

Both key the file off `payload.session_id` (read directly). Decision: Claude
always supplies it and this is a Claude-only feature, so we skip the repo's
`_host_env.session_id()` helper for now (possible future swap if upstreamed).

## Increments (test-first)

1. test: concurrent bumps lose no increments → impl: make the counter
   append-one-line-per-flag (atomic append), `read_count` counts lines.
2. test: gradient moves toward red across ALL three RGB channels (not just green)
   + exact endpoints → impl: (test-only strengthening; core already correct).
3. test: bump(session) then statusline render for the SAME session shows the
   count (writer↔reader agree on the key), real file no stub → impl: as needed.
4. test: hook main() bumps 0→1 when an advisory fires, stays 0 when silent →
   impl: (line already added to main(); this covers it).
5. wiring + docs: statusline settings snippet (user pastes manually — bash guard
   blocks editing settings.json) + a section in docs/dup-oracle.md.

## Notes

- Non-blocker follow-ups: old per-session counter files linger in tmp (OS clears
  tmp eventually); possible future swap to `_host_env.session_id()`.
- `.claude/settings.json` `statusLine` is unset and the bash guard blocks writes
  to it — wiring is a manual user step.
