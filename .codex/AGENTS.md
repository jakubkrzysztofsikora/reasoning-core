# Codex agent instructions — reasoning-core

This repository runs a System 2 sidecar (HTTP `127.0.0.1:8765`) that scores
proposed code edits and emits an `ImpactReport`. Codex 0.144.5+ supports
PreToolUse and Stop hooks; the `pre_edit_guard.py` and `stop_reconcile.py`
hooks are wired in via `~/.codex/hooks.json`.

## Required: trust the hooks

Before running any non-trivial work in this repo, open `codex /hooks` and
trust the reasoning-core hook definitions (Codex hashes hook commands and
won't run them until trusted).

## Risk vector interpretation

See `.codex/skills/reasoning/SKILL.md` (if present) or
`docs/HOW_IT_WORKS.md` for the 8-dimension risk vector mapping
(cyclomatic, fan_in, fan_out, depth, churn, coupling, cohesion, novelty)
and the regression-detection thresholds.

## Stop hook semantics

Codex `Stop` hooks run after each turn. The `stop_reconcile.py` hook diffs
the working tree against `gate_edit` audit rows. If you wrote files without
calling `gate_edit` first, the hook returns `decision: "block"` so Codex
auto-continues with a continuation prompt to call the gate. Do not bypass.
