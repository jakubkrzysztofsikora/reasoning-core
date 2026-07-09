#!/usr/bin/env bash
# Quick-enable reasoning-core hooks for Moonshot Kimi CLI in the current repo.
#
# Materialises:
#   .kimi/settings.json            — generated from .kimi/settings.json.template
#                                    with <RC_REPO> substituted to absolute path
#                                    (gitignored: per-machine, not committed)
#   .envrc additions               — RC_HOST=kimi if .envrc exists
#
# Idempotent: refuses to overwrite an existing .kimi/settings.json unless
# the user passes --force.
set -euo pipefail

if [[ -z "${RC_REPO:-}" ]]; then
  echo "error: RC_REPO is unset. Export it to your reasoning-core checkout:"
  echo "  export RC_REPO=\$HOME/Repos/personal/reasoning-core"
  exit 2
fi
if [[ ! -d "$RC_REPO/src/hooks" ]]; then
  echo "error: RC_REPO=$RC_REPO does not contain src/hooks/" >&2
  exit 2
fi
if [[ ! -f "$RC_REPO/.kimi/settings.json.template" ]]; then
  echo "error: template missing at $RC_REPO/.kimi/settings.json.template" >&2
  exit 2
fi
if ! command -v kimi >/dev/null 2>&1; then
  echo "warn: kimi CLI not found on PATH. Install via Moonshot Kimi CLI package."
fi

target_dir=".kimi"
target_settings="$target_dir/settings.json"
template="$RC_REPO/.kimi/settings.json.template"

force=0
if [[ "${1:-}" == "--force" ]]; then force=1; fi

if [[ -e "$target_settings" && $force -ne 1 ]]; then
  echo "error: $target_settings already exists. Pass --force to overwrite." >&2
  exit 1
fi

mkdir -p "$target_dir"

# Substitute <RC_REPO> with the absolute path. Use a delimiter that isn't /
sed "s|<RC_REPO>|$RC_REPO|g" "$template" > "$target_settings"

# Validate JSON
python3 -c "import json,sys; json.load(open('$target_settings'))" >/dev/null

# Ensure RC_HOST is exported in this repo
if [[ -f .envrc ]]; then
  if ! grep -q '^export RC_HOST=' .envrc; then
    echo 'export RC_HOST=kimi' >> .envrc
    echo "added 'export RC_HOST=kimi' to .envrc"
  fi
fi

# .gitignore entries
if [[ -f .gitignore ]]; then
  grep -qxF '.kimi/settings.json' .gitignore || echo '.kimi/settings.json' >> .gitignore
fi

cat <<DONE
wrote $target_settings  (gitignored, per-machine)
template lives at $RC_REPO/.kimi/settings.json.template (committed)

next:
  curl -fsS http://127.0.0.1:8765/health   # confirm sidecar
  kimi "say hi"                            # smoke-test hooks
DONE
