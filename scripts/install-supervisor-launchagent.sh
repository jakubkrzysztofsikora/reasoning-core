#!/usr/bin/env bash
#
# install-supervisor-launchagent.sh
#
# Install the reasoning-core sidecar supervisor as a per-user launchd LaunchAgent.
# Substitutes the __REPO__ placeholder in the plist with the current repo path,
# copies it into ~/Library/LaunchAgents, and (re)loads it.
#
# Run from the repository root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC_PLIST="${REPO}/launchd/com.reasoning-core.supervisor.plist"
LABEL="com.reasoning-core.supervisor"
DEST_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${DEST_DIR}/${LABEL}.plist"

if [[ ! -f "${SRC_PLIST}" ]]; then
    echo "error: source plist not found at ${SRC_PLIST}" >&2
    exit 1
fi

if [[ ! -x "${REPO}/.venv/bin/python" ]]; then
    echo "warn: ${REPO}/.venv/bin/python not found or not executable" >&2
    echo "      create the venv before launchd will be able to run the agent" >&2
fi

mkdir -p "${DEST_DIR}"

# Use the same renderer and domain-aware loader as rc init.
PYTHONPATH="${REPO}:${REPO}/src" "${REPO}/.venv/bin/python" - <<'PY'
import sys
from pathlib import Path
from src._init import InitResult, _install_launchd

result = InitResult(target=Path.cwd())
if not _install_launchd(sys.executable, result):
    for warning in result.warned:
        print(warning, file=sys.stderr)
    raise SystemExit(1)
print("installed and registered: com.reasoning-core.supervisor")
PY

cat <<EOF

Next steps:
  status: launchctl list | grep reasoning-core
  logs:   tail -f /tmp/rc-supervisor.out.log /tmp/rc-supervisor.err.log
  stop:   launchctl unload "${DEST_PLIST}"
EOF
