# reasoning-core — GitHub Copilot CLI instructions

This repository ships a System 2 sidecar (HTTP `127.0.0.1:8765`) that
scores proposed code edits and emits an `ImpactReport`. The Copilot CLI
exposes the sidecar via the `hybrid-reasoner` MCP server (registered in
`~/.copilot/mcp-config.json` by `scripts/enable-in-repo-copilot.sh`).

**Before any write or edit**, call the MCP tool `gate_edit` with the
target path and pre/post sources. If `gate_edit` returns
`decision: "block"`, do NOT proceed; surface the `message` verbatim to
the user and either justify the architectural pivot or revert.

The Copilot CLI does not yet expose runtime hooks, so this instruction
file is the only contract enforcing pre-edit gating. The post-turn
audit (`gate_edit` MCP rows in `~/.local/share/reasoning-core/events/`)
will retroactively flag missed calls.

## Trust + headless

For non-interactive runs use `copilot --allow-all-tools` (or env
`COPILOT_ALLOW_ALL=1`). MCP server trust is granted on first use; ensure
`~/.copilot/mcp-config.json` already includes `hybrid-reasoner` before
launching headless.

## Risk vector interpretation

See `.copilot/skills/reasoning/SKILL.md` for the 8-dimension risk vector
mapping (cyclomatic, fan_in, fan_out, depth, churn, coupling, cohesion,
novelty) and the regression-detection thresholds.
