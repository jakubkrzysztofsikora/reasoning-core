#!/usr/bin/env bash
#
# install-daily-benchmark-launchagent.sh
#
# Install the reasoning-core daily benchmark service as a per-user launchd
# LaunchAgent. Substitutes __REPO__ placeholders, copies the plist into
# ~/Library/LaunchAgents, and loads it.
#
# Run from the repository root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC_PLIST="${REPO}/launchd/com.reasoning-core.daily-benchmark.plist"
LABEL="com.reasoning-core.daily-benchmark"
DEST_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${DEST_DIR}/${LABEL}.plist"

if [[ ! -f "${SRC_PLIST}" ]]; then
    echo "error: source plist not found at ${SRC_PLIST}" >&2
    exit 1
fi

if [[ ! -x "${REPO}/scripts/daily-benchmark.sh" ]]; then
    echo "error: benchmark script not executable at ${REPO}/scripts/daily-benchmark.sh" >&2
    exit 1
fi

mkdir -p "${DEST_DIR}"

sed -e "s|__REPO__|${REPO}|g" -e "s|__HOME__|${HOME}|g" "${SRC_PLIST}" > "${DEST_PLIST}"
echo "installed: ${DEST_PLIST}"

plutil -lint "${DEST_PLIST}"

launchctl unload "${DEST_PLIST}" 2>/dev/null || true
launchctl load -w "${DEST_PLIST}"
echo "loaded: ${LABEL}"

cat <<EOF

Daily benchmark service installed.
  runs:   06:17 local time every day
  logs:   tail -f /tmp/rc-daily-benchmark.out.log /tmp/rc-daily-benchmark.err.log
  output: ~/.local/share/reasoning-core/benchmarks/
  stop:   launchctl unload "${DEST_PLIST}"
  run now: launchctl start ${LABEL}
EOF
