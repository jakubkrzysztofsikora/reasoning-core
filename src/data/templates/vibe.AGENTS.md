# Vibe agent instructions — reasoning-core

This repository runs a System 2 sidecar (HTTP `127.0.0.1:8765`) that scores
proposed code edits and emits an `ImpactReport`. Vibe v2.9.4 lacks runtime
PreToolUse hooks, so the gate is enforced at the model layer via the
`hybrid-reasoner` MCP server.

## Required: gate every write

**Before any write or edit**, you MUST call the MCP tool
`hybrid_reasoner_gate_edit` with the target path, current contents
(`before_src`), and proposed contents (`after_src`).

If the tool returns `decision: "block"`, do NOT proceed. Surface the
returned `message` verbatim to the user, then either justify the
architectural pivot (operator must explicitly approve) or revert your
proposed change.

If the sidecar is unreachable and `S2_FAIL_CLOSED=1` (default for this
repo), `gate_edit` returns `decision: "block"` — do not bypass.

## Risk vector interpretation

See `.vibe/skills/reasoning/SKILL.md` for the 8-dimension risk vector
mapping (cyclomatic, fan_in, fan_out, depth, churn, coupling, cohesion,
novelty) and the regression-detection thresholds.

## Headless / eval

Use `vibe --prompt "..." --trust` for non-interactive runs. The repo's
`.envrc` sets `RC_HOST=vibe`. Sidecar must already be running.
