#!/usr/bin/env bash
# reasoning-core — revert install in the current repo.
#
# Reads `.reasoning-core/install.manifest` (written by install.sh) and removes
# exactly the per-repo paths that were created. Also strips `hybrid-reasoner`
# from `~/.copilot/mcp-config.json`, drops this repo's entry from
# `~/.vibe/trusted_folders.toml`, and removes the reasoning-core block from
# `.gitignore` (between the sentinel comments install.sh added).
#
# Paths the user created themselves (or that pre-existed before install) are
# preserved.

set -euo pipefail

TARGET_REPO="$(pwd)"
MANIFEST=".reasoning-core/install.manifest"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
skip() { printf '  \033[33m·\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*" >&2; }

if [[ ! -f "$MANIFEST" ]]; then
  warn "no install manifest at $MANIFEST"
  warn "if you installed manually, delete .envrc / .claude/settings.local.json /"
  warn "  .gemini/settings.json / .copilot/ / .vibe/ by hand."
  exit 1
fi

printf '\nreasoning-core uninstall\n  TARGET_REPO = %s\n\n' "$TARGET_REPO"

# ---------------------------------------------------------------------------
# 1. Per-repo files listed in the manifest
# ---------------------------------------------------------------------------
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  if [[ -e "$path" ]]; then
    rm -rf "$path"
    ok "removed $path"
  else
    skip "$path already gone"
  fi
done < "$MANIFEST"

# Tidy empty parent dirs (.claude, .gemini, .copilot, .vibe). `find -depth`
# processes children before parents so an empty tree collapses bottom-up.
for root in .claude .gemini .copilot .vibe; do
  [[ -d "$root" ]] && find "$root" -depth -type d -empty -delete 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 2. ~/.copilot/mcp-config.json — drop hybrid-reasoner
# ---------------------------------------------------------------------------
copilot_cfg="$HOME/.copilot/mcp-config.json"
if [[ -f "$copilot_cfg" ]]; then
  python3 - "$copilot_cfg" <<'PYEOF'
import json, os, sys
p = sys.argv[1]
try:
    data = json.load(open(p))
except Exception:
    sys.exit(0)
servers = data.get("mcpServers", {})
if "hybrid-reasoner" in servers:
    del servers["hybrid-reasoner"]
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.rename(tmp, p)
    print(f"  \033[32m✓\033[0m stripped hybrid-reasoner from {p}")
else:
    print(f"  \033[33m·\033[0m hybrid-reasoner already absent from {p}")
PYEOF
fi

# ---------------------------------------------------------------------------
# 3. ~/.vibe/trusted_folders.toml — drop this repo's entry
# ---------------------------------------------------------------------------
vibe_trusted="$HOME/.vibe/trusted_folders.toml"
if [[ -f "$vibe_trusted" ]]; then
  python3 - "$vibe_trusted" "$TARGET_REPO" <<'PYEOF'
import os, re, sys
p, here = sys.argv[1], sys.argv[2]
with open(p) as fh:
    text = fh.read()
# Match an entire [[trusted]] block whose path = "<here>" (any quote style).
pattern = re.compile(
    r'\n?\[\[trusted\]\]\s*\npath\s*=\s*["\']?' + re.escape(here) + r'["\']?\s*\n?',
    re.MULTILINE,
)
new = pattern.sub('\n', text).lstrip('\n')
if new != text:
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(new)
    os.rename(tmp, p)
    print(f"  \033[32m✓\033[0m removed {here} from {p}")
else:
    print(f"  \033[33m·\033[0m {here} not in {p}")
PYEOF
fi

# ---------------------------------------------------------------------------
# 4. .gitignore — drop the reasoning-core sentinel block
# ---------------------------------------------------------------------------
if [[ -f .gitignore ]] && grep -qxF '# >>> reasoning-core >>>' .gitignore; then
  python3 - .gitignore <<'PYEOF'
import os, re, sys
p = sys.argv[1]
with open(p) as fh:
    text = fh.read()
new = re.sub(
    r'\n?# >>> reasoning-core >>>\n.*?# <<< reasoning-core <<<\n?',
    '',
    text,
    flags=re.DOTALL,
)
if new != text:
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(new)
    os.rename(tmp, p)
    print(f"  \033[32m✓\033[0m cleaned reasoning-core block from {p}")
PYEOF
fi

# ---------------------------------------------------------------------------
# 5. Manifest dir
# ---------------------------------------------------------------------------
rm -rf .reasoning-core
ok "removed .reasoning-core/"

cat <<EOF

done. reasoning-core no longer wires hooks into this repo.
the shared clone, the sidecar, and Python deps are untouched — remove those
manually if you also want to uninstall the framework itself.
EOF
