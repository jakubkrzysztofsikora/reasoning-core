#!/usr/bin/env bash
# enable-in-repo-codex.sh — install reasoning-core hooks into Codex CLI for a repo.
#
# Writes `~/.codex/hooks.json` (global) so Codex runs the reasoning-core hooks
# for every session, plus an `AGENTS.md` note for the agent. Codex 0.144.5+
# discovers hooks from ~/.codex/hooks.json, ~/.codex/config.toml, or
# <repo>/.codex/{hooks.json,config.toml}.
#
# Codex requires non-managed command hooks to be reviewed and trusted before
# they run. Run `codex /hooks` after install to trust the hooks.
#
# Usage:
#   bash scripts/enable-in-repo-codex.sh                   # install globally
#   RC_REPO=/abs/path bash scripts/enable-in-repo-codex.sh  # explicit repo
set -euo pipefail

RC_REPO="${RC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if [ ! -d "$RC_REPO/src/hooks" ]; then
  echo "could not find $RC_REPO/src/hooks" >&2
  exit 1
fi

mkdir -p ~/.codex

# Write global hooks.json. Codex loads hooks from this file regardless of cwd.
sed "s|<RC_REPO>|$RC_REPO|g" "$RC_REPO/.codex/settings.json.template" > ~/.codex/hooks.json

# Append a Codex-formatted inline [[hooks]] block to config.toml if not already present.
CODEX_CONFIG=~/.codex/config.toml
if [ -f "$CODEX_CONFIG" ] && grep -q "\[\[hooks\.PreToolUse\]\]" "$CODEX_CONFIG"; then
  echo "config.toml already has reasoning-core hooks block"
else
  cat >> "$CODEX_CONFIG" <<EOF

# --- reasoning-core hooks (Codex CLI 0.144.5+, see $RC_REPO/.codex/settings.json.template) ---
[[hooks.PreToolUse]]
matcher = "apply_patch|Edit|Write"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "python3 $RC_REPO/src/hooks/pre_edit_guard.py"
timeout = 60

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "python3 $RC_REPO/src/hooks/stop_reconcile.py"
timeout = 30
EOF
fi

# Write AGENTS.md for this repo so the Codex agent knows to honor the gate
# even if a hook is bypassed.
mkdir -p "$RC_REPO/.codex"
cat > "$RC_REPO/.codex/AGENTS.md" <<EOF
# Codex agent instructions — reasoning-core

This repository runs a System 2 sidecar (HTTP \`127.0.0.1:8765\`) that scores
proposed code edits and emits an \`ImpactReport\`. Codex 0.144.5+ supports
PreToolUse and Stop hooks; the \`pre_edit_guard.py\` and \`stop_reconcile.py\`
hooks are wired in via \`~/.codex/hooks.json\`.

## Required: trust the hooks

Before running any non-trivial work in this repo, open \`codex /hooks\` and
trust the reasoning-core hook definitions (Codex hashes hook commands and
won't run them until trusted).

## Risk vector interpretation

See \`.codex/skills/reasoning/SKILL.md\` (if present) or
\`docs/HOW_IT_WORKS.md\` for the 8-dimension risk vector mapping
(cyclomatic, fan_in, fan_out, depth, churn, coupling, cohesion, novelty)
and the regression-detection thresholds.

## Stop hook semantics

Codex \`Stop\` hooks run after each turn. The \`stop_reconcile.py\` hook diffs
the working tree against \`gate_edit\` audit rows. If you wrote files without
calling \`gate_edit\` first, the hook returns \`decision: "block"\` so Codex
auto-continues with a continuation prompt to call the gate. Do not bypass.
EOF

echo "wrote ~/.codex/hooks.json and $CODEX_CONFIG"
echo "wrote $RC_REPO/.codex/AGENTS.md"
echo
echo "next: open Codex and run /hooks to trust the new hook definitions."
echo "sidecar must be running (RC_REPO sidecar_supervisor.py on 127.0.0.1:8765)."
