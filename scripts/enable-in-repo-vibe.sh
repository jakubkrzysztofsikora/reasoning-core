#!/usr/bin/env bash
# Quick-enable reasoning-core for Mistral Vibe CLI.
#
# Materialises:
#   .vibe/config.toml — generated from template (gitignored, per-machine)
#   .vibe/AGENTS.md   — Vibe-scoped gate_edit instruction (committed)
#   ~/.vibe/trusted_folders.toml — ensure cwd is trusted (best-effort)
set -euo pipefail

if [[ -z "${RC_REPO:-}" ]]; then
  echo "error: RC_REPO is unset." >&2
  exit 2
fi
if [[ ! -f "$RC_REPO/.vibe/config.toml.template" ]]; then
  echo "error: $RC_REPO/.vibe/config.toml.template missing" >&2
  exit 2
fi
if ! command -v vibe >/dev/null 2>&1; then
  echo "warn: vibe CLI not found on PATH. Phase 4 install requires vibe >= v2.9.4."
fi

force=0
[[ "${1:-}" == "--force" ]] && force=1

mkdir -p .vibe
target=".vibe/config.toml"
if [[ -e "$target" && $force -ne 1 ]]; then
  echo "error: $target exists. Pass --force to overwrite." >&2
  exit 1
fi
sed "s|<RC_REPO>|$RC_REPO|g" "$RC_REPO/.vibe/config.toml.template" > "$target"

# AGENTS.md / SKILL.md committed in this repo, but copy from RC_REPO if
# the user is enabling in a different repo.
mkdir -p .vibe/skills/reasoning
cp -n "$RC_REPO/.vibe/AGENTS.md" .vibe/AGENTS.md 2>/dev/null || true
cp -n "$RC_REPO/.vibe/skills/reasoning/SKILL.md" .vibe/skills/reasoning/SKILL.md 2>/dev/null || true

# Trusted-folder registration (best-effort)
trusted="$HOME/.vibe/trusted_folders.toml"
mkdir -p "$HOME/.vibe"
if ! grep -qF "$(pwd)" "$trusted" 2>/dev/null; then
  printf '\n[[trusted]]\npath = %q\n' "$(pwd)" >> "$trusted"
  echo "added $(pwd) to ~/.vibe/trusted_folders.toml"
fi

# Set RC_HOST in .envrc if present
if [[ -f .envrc ]]; then
  if ! grep -q '^export RC_HOST=' .envrc; then
    echo 'export RC_HOST=vibe' >> .envrc
    echo "added 'export RC_HOST=vibe' to .envrc"
  fi
fi

# .gitignore
if [[ -f .gitignore ]]; then
  grep -qxF '.vibe/config.toml' .gitignore || echo '.vibe/config.toml' >> .gitignore
fi

cat <<DONE
wrote $target  (gitignored, per-machine)
template lives at $RC_REPO/.vibe/config.toml.template (committed)

next:
  curl -fsS http://127.0.0.1:8765/health     # confirm sidecar
  vibe --prompt "say hi" --trust              # smoke-test, --trust skips trust prompt
DONE
