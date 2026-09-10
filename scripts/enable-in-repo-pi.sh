#!/usr/bin/env bash
# Quick‑enable reasoning‑core for Pi CLI.
# Materialises per‑machine .pi config, copies gate extension, and
# registers the repository as a trusted folder.

set -euo pipefail

if [[ -z "${RC_REPO:-}" ]]; then
  echo "error: RC_REPO is unset." >&2
  exit 2
fi
if [[ ! -f "$RC_REPO/.pi/settings.json.template" ]]; then
  echo "error: $RC_REPO/.pi/settings.json.template missing" >&2
  exit 2
fi
if ! command -v pi >/dev/null 2>&1; then
  echo "warn: pi CLI not found on PATH. Install via npm or bun first." >&2
fi

force=0
[[ "${1:-}" == "--force" ]] && force=1

mkdir -p .pi
target=".pi/settings.json"
if [[ -e "$target" && $force -ne 1 ]]; then
  echo "error: $target exists. Pass --force to overwrite." >&2
  exit 1
fi
sed "s|<RC_REPO>|$RC_REPO|g" "$RC_REPO/.pi/settings.json.template" > "$target"

# Copy extension – use -n to avoid overwriting user edits.
mkdir -p .pi/extensions
cp -n "$RC_REPO/.pi/extensions/reasoning_core_gate.ts" .pi/extensions/ 2>/dev/null || true

# Register trusted folder (Pi prompts for trust on interactive runs).
trusted="$HOME/.pi/trusted_folders.toml"
mkdir -p "$(dirname "$trusted")"
pwd_line=$(printf 'path = %q' "$(pwd)")
if ! grep -qxF "$pwd_line" "$trusted" 2>/dev/null; then
  printf '\n[[trusted]]\npath = %q\n' "$(pwd)" >> "$trusted"
  echo "added $(pwd) to $trusted"
fi

# Set RC_HOST in .envrc if present.
if [[ -f .envrc ]]; then
  if ! grep -q '^export RC_HOST=' .envrc; then
    echo 'export RC_HOST=pi' >> .envrc
    echo "added 'export RC_HOST=pi' to .envrc"
  fi
fi

# .gitignore entry for the generated settings.
if [[ -f .gitignore ]]; then
  grep -qxF '.pi/settings.json' .gitignore || echo '.pi/settings.json' >> .gitignore
fi

cat <<DONE
wrote $target (gitignored, per‑machine)
next steps:
  curl -fsS http://127.0.0.1:8765/health   # confirm sidecar
  pi --prompt "say hi"                     # smoke test (trust prompt will appear)
DONE
