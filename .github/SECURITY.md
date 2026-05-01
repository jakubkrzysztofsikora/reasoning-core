# Security policy

## Reporting a vulnerability

Please open a private security advisory via GitHub's "Report a vulnerability"
flow on the repository's Security tab. Do not open public issues for
security-relevant bugs.

## Policy-as-code: hooks are part of the security boundary

The PreToolUse / PostToolUse hooks under `src/hooks/` -- specifically
`pre_edit_guard.py`, `pre_bash_guard.py`, and `post_bash_revive.py` -- are
*policy code*, not auxiliary tooling. They sit between an autonomous agent and
the filesystem. A weakening of these hooks is, by definition, a weakening of
the safety surface this project exists to provide.

Treat them with the same review discipline you would apply to an auth
middleware or a permissions check.

### Required for any change under `src/hooks/` or `.claude/settings.json`

1. **Reviewed pull request.** No direct pushes to `main`, no self-merges. At
   least one human reviewer must read the diff end-to-end.
2. **Tests must not regress.** The guard test files
   (`tests/test_pre_bash_guard.py`, `tests/test_hook_block.py`, and any
   future `tests/test_*_guard.py`) are the spec. A PR that **decreases**
   the number of asserted bypass paths -- or flips a previously-blocked
   pattern to allow -- will not be merged absent an explicit, written
   threat-model justification in the PR description.
3. **Document the threat model delta.** If the change adds a new bypass
   class the guard now defends against, add a row to `docs/HARDENING.md`
   "Threat model" table. If the change removes a guard, document *why* in
   the same table and link the PR.
4. **Override env discipline.** `RC_ALLOW_GUARD_EDIT=1` is the documented
   maintenance escape hatch. Do not introduce parallel override paths
   (per-file allow-lists, time-bounded bypasses, "trusted user" flags).
   One toggle, binary, restart-scoped.

### Files in scope

`src/hooks/pre_edit_guard.py`, `src/hooks/pre_bash_guard.py`,
`src/hooks/post_bash_revive.py`, `src/hooks/audit_log.py`,
`.claude/settings.json`, `.claude/settings.local.json`,
`scripts/start-sidecar.sh`, plus the guard tests under `tests/`.

The guarded-path list itself lives in `pre_edit_guard.py` (`GUARDED_PATHS`)
and is mirrored in `docs/HARDENING.md`. Keep both in sync; a drift between
the documented list and the runtime list is a reviewable defect.

### CI gate

The `lint-and-test` workflow runs every PR through `pytest -m "not live"`,
which includes the guard test suites. A failing guard test blocks merge.
This is the minimum bar; reviewers are expected to read the diff in
addition to letting CI run.
