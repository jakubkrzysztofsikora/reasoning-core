#!/usr/bin/env bash
# Quick-enable reasoning-core for GitHub Copilot CLI.
#
# Verification (2026-05-08): copilot v1.0.29 has NO hook subcommand; Phase 3
# is MCP + skill + instructions only. Runtime gating is the gate_edit MCP
# tool (Phase 4).
#
# Materialises:
#   ~/.copilot/mcp-config.json — MERGE in `hybrid-reasoner` entry
#                                (preserves existing user MCP servers)
#   .copilot/                  — local skills + instructions copy
set -euo pipefail

if [[ -z "${RC_REPO:-}" ]]; then
  echo "error: RC_REPO is unset. Export it to your reasoning-core checkout." >&2
  exit 2
fi
if [[ ! -d "$RC_REPO/.copilot" ]]; then
  echo "error: $RC_REPO/.copilot scaffold missing" >&2
  exit 2
fi
if ! command -v copilot >/dev/null 2>&1; then
  echo "warn: copilot CLI not found on PATH. Phase 3 install requires copilot >= v1.0.29."
fi

mkdir -p .copilot
cp -n "$RC_REPO/.copilot/copilot-instructions.md" .copilot/copilot-instructions.md 2>/dev/null || true
mkdir -p .copilot/skills/reasoning
cp -n "$RC_REPO/.copilot/skills/reasoning/SKILL.md" .copilot/skills/reasoning/SKILL.md 2>/dev/null || true

# Merge into ~/.copilot/mcp-config.json with file-lock
target="$HOME/.copilot/mcp-config.json"
mkdir -p "$HOME/.copilot"
ts=$(date +%s)
[[ -f "$target" ]] && cp "$target" "$target.bak.$ts"

python3 - "$target" "$RC_REPO" <<'PYEOF'
import json, os, sys
target_path, rc_repo = sys.argv[1], sys.argv[2]
existing = {}
if os.path.exists(target_path):
    try:
        existing = json.load(open(target_path))
    except Exception:
        existing = {}
servers = existing.setdefault("mcpServers", {})
servers["hybrid-reasoner"] = {
    "command": "python3",
    "args": ["-m", "src.mcp_reasoner"],
    "cwd": rc_repo,
    "env": {},
}
# Atomic write: temp + rename
tmp = target_path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(existing, fh, indent=2, sort_keys=True)
os.rename(tmp, target_path)
print(f"merged hybrid-reasoner into {target_path}")
PYEOF

# Set RC_HOST in .envrc if present
if [[ -f .envrc ]]; then
  if ! grep -q '^export RC_HOST=' .envrc; then
    echo 'export RC_HOST=copilot' >> .envrc
    echo "added 'export RC_HOST=copilot' to .envrc"
  fi
fi

cat <<DONE
wrote .copilot/copilot-instructions.md
wrote .copilot/skills/reasoning/SKILL.md
merged hybrid-reasoner into $target

next:
  curl -fsS http://127.0.0.1:8765/health         # confirm sidecar
  copilot --allow-all-tools "say hi"             # smoke-test (env: COPILOT_ALLOW_ALL=1)
DONE
