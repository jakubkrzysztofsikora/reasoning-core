# reasoning-core — Gemini CLI context

This repository runs the System 2 sidecar (HTTP `127.0.0.1:8765`) that scores
proposed code edits and emits an `ImpactReport`. Hooks in `.gemini/settings.json`
gate `write_file`, `edit_file`, and `run_shell_command` through that sidecar.

When the sidecar reports `regression_detected: true`, the PreToolUse hook
blocks the edit and surfaces a one-paragraph human summary explaining why.
Use the `reasoning` skill (under `.gemini/skills/reasoning/SKILL.md`) to
interpret the 8-dimension risk vector and decide whether to retry, justify,
or revert the change.

## Sidecar lifecycle

The sidecar must be running before `gemini` starts a session in this repo.
Bring it up with `bash scripts/start-sidecar.sh`. Confirm with
`curl -fsS http://127.0.0.1:8765/health`. With `S2_FAIL_CLOSED=1` (the
default), unreachable sidecar means edits are blocked rather than waved
through silently.

## Trust prompt

First MCP server load may surface a `Trust this MCP server? y/N` prompt.
For headless/eval runs, pass `--yolo` or `--approval-mode yolo`, or
pre-populate `~/.gemini/trusted_mcp.json`.
