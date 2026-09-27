#!/usr/bin/env bash
# promote-all-repos.sh — Promote all installed reasoning-core repos to copilot enforcement.
#
# Finds every repo with a .reasoning-core/install.manifest and ensures its
# .envrc.local contains the correct enforcement block. Idempotent: repos that
# already have the full correct block are skipped.
#
# Usage:
#   bash scripts/promote-all-repos.sh              # all repos under ~/Repos
#   bash scripts/promote-all-repos.sh /path/to     # specific parent directory
#
# The reasoning-core checkout itself is excluded (it has its own .envrc.local
# with development defaults).
set -euo pipefail

SEARCH_ROOT="${1:-$HOME/Repos}"

SENTINEL_START="# >>> reasoning-core enforcement pilot (2026-07-10) >>>"
SENTINEL_END="# <<< reasoning-core enforcement pilot (2026-07-10) <<<"

ENFORCEMENT_BLOCK="$SENTINEL_START
# Activate intentionally with: direnv reload
# This block promotes the repo from advise (log/warn) to copilot (block) and
# turns plan-grounding drift into a hard block (RC_PLAN_GROUNDING=2).
# It is gitignored by default and must not be committed.
export RC_MODE=copilot
export RC_SHADOW_MODE=0
export RC_PLAN_BLOCK=1
export RC_PLAN_GROUNDING=2
export RC_ORACLE_BLOCK=1
export RC_RULE_ENGINE=1
export S2_FAIL_CLOSED=1
$SENTINEL_END"

promoted=0
skipped=0
created=0
updated=0

while IFS= read -r manifest; do
  repo="$(dirname "$(dirname "$manifest")")"
  envrc_local="$repo/.envrc.local"

  # Skip reasoning-core itself
  if [[ "$repo" == *"reasoning-core"* ]]; then
    continue
  fi

  name="$(basename "$repo")"

  # Check if already has correct enforcement block
  if [[ -f "$envrc_local" ]] && grep -qF "$SENTINEL_START" "$envrc_local" 2>/dev/null; then
    if grep -q "RC_MODE=copilot" "$envrc_local" && \
       grep -q "RC_SHADOW_MODE=0" "$envrc_local" && \
       grep -q "RC_PLAN_BLOCK=1" "$envrc_local" && \
       grep -q "RC_PLAN_GROUNDING=2" "$envrc_local" && \
       grep -q "RC_RULE_ENGINE=1" "$envrc_local" && \
       grep -q "S2_FAIL_CLOSED=1" "$envrc_local"; then
      echo "OK:      $name"
      ((skipped++)) || true
      continue
    else
      echo "STALE:   $name — updating enforcement block"
    fi
  fi

  # Write or append the enforcement block
  if [[ -f "$envrc_local" ]]; then
    # Remove any existing (possibly stale) block, then append fresh
    cleaned=$(python3 -c "
import re, sys
text = open(sys.argv[1]).read()
pattern = re.compile(r'\n?# >>> reasoning-core enforcement pilot.*?# <<< reasoning-core enforcement pilot.*?\n?', re.DOTALL)
cleaned = pattern.sub('\n', text).strip('\n')
print(cleaned)
" "$envrc_local")
    if [[ -n "$cleaned" ]]; then
      printf '%s\n\n%s\n' "$cleaned" "$ENFORCEMENT_BLOCK" > "$envrc_local"
    else
      printf '%s\n' "$ENFORCEMENT_BLOCK" > "$envrc_local"
    fi
    echo "UPDATED: $name"
    ((updated++)) || true
  else
    printf '%s\n' "$ENFORCEMENT_BLOCK" > "$envrc_local"
    echo "CREATED: $name"
    ((created++)) || true
  fi
done < <(find "$SEARCH_ROOT" -maxdepth 5 -name install.manifest -path '*/.reasoning-core/*' 2>/dev/null)

echo ""
echo "=== Summary ==="
echo "Already correct: $skipped"
echo "Updated:         $updated"
echo "Created new:     $created"
echo "Total processed: $((skipped + updated + created))"
