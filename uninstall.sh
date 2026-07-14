#!/usr/bin/env bash
# reasoning-core — revert install in the current repo.
#
# Reads `.reasoning-core/install.manifest` (written by install.sh) and removes
# exactly the per-repo paths that were created — refusing to delete anything
# that resolves outside the current target repo. Also strips `hybrid-reasoner`
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
#    Refuses anything that resolves outside TARGET_REPO — defends against
#    a tampered/corrupt manifest.
# ---------------------------------------------------------------------------
TARGET_REPO="$TARGET_REPO" MANIFEST="$MANIFEST" python3 <<'PYEOF'
import os, shutil, sys
target = os.path.realpath(os.environ["TARGET_REPO"])
manifest = os.environ["MANIFEST"]
with open(manifest) as fh:
    entries = [line.strip() for line in fh if line.strip()]

GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; RESET = "\033[0m"

for entry in entries:
    abs_path = os.path.realpath(os.path.join(target, entry))
    # Anchor under TARGET_REPO. commonpath rejects absolute-elsewhere AND
    # any `..` escape.
    try:
        common = os.path.commonpath([abs_path, target])
    except ValueError:
        common = ""
    if common != target:
        print(f"  {RED}✗{RESET} refusing to remove out-of-tree path: {entry!r} -> {abs_path}")
        continue
    if abs_path == target:
        print(f"  {RED}✗{RESET} refusing to remove the repo root itself ({entry!r})")
        continue
    if not os.path.lexists(abs_path):
        print(f"  {YELLOW}·{RESET} {entry} already gone")
        continue
    if os.path.isdir(abs_path) and not os.path.islink(abs_path):
        shutil.rmtree(abs_path)
    else:
        os.unlink(abs_path)
    print(f"  {GREEN}✓{RESET} removed {entry}")
PYEOF

# Tidy empty parent dirs (.claude, .codex, .gemini, .copilot, .kimi, .vibe).
# `find -depth` processes children before parents so an empty tree collapses
# bottom-up. Keep this list in sync with the per-CLI directories install.sh
# `mkdir -p`s; missing any one of them leaves an empty dir on disk and trips
# the multi-cli smoke gate.
for root in .claude .codex .gemini .copilot .kimi .vibe; do
  [[ -d "$root" ]] && find "$root" -depth -type d -empty -delete 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 2. ~/.copilot/mcp-config.json — drop hybrid-reasoner + the lockfile
# ---------------------------------------------------------------------------
copilot_cfg="$HOME/.copilot/mcp-config.json"
if [[ -f "$copilot_cfg" ]]; then
  COPILOT_CFG="$copilot_cfg" python3 <<'PYEOF'
import json, os, sys
p = os.environ["COPILOT_CFG"]
try:
    with open(p) as fh:
        data = json.load(fh)
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
# install.sh creates a sibling .lock file via flock — clean it up too.
if [[ -f "$copilot_cfg.lock" ]]; then
  rm -f "$copilot_cfg.lock"
  ok "removed $copilot_cfg.lock"
fi

# ---------------------------------------------------------------------------
# 3. ~/.vibe/trusted_folders.toml — drop this repo's entry
# ---------------------------------------------------------------------------
vibe_trusted="$HOME/.vibe/trusted_folders.toml"
if [[ -f "$vibe_trusted" ]]; then
  VIBE_TRUSTED="$vibe_trusted" HERE="$TARGET_REPO" python3 <<'PYEOF'
import os, re
p, here = os.environ["VIBE_TRUSTED"], os.environ["HERE"]
with open(p) as fh:
    text = fh.read()
# Match an entire [[trusted]] block whose path = "<here>" (any quote style,
# tolerant of the legacy `%q`-escaped install.sh output).
pattern = re.compile(
    r'\n?\[\[trusted\]\]\s*\npath\s*=\s*["\']?' + re.escape(here) + r'["\']?\s*(?:\n|$)',
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
  python3 <<'PYEOF'
import os, re
p = ".gitignore"
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
