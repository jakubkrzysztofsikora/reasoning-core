#!/usr/bin/env bash
# start-sidecar.sh -- boot the System 2 reasoning sidecar on 127.0.0.1:8765.
#
# Behavior:
#   1. Launches `python3 -m src.s2_core` in the foreground by default, or in
#      the background if BACKGROUND=1 is set.
#   2. When BACKGROUND=1, polls GET /health until model_loaded:true (or a
#      timeout) before exiting 0. This lets test-prototype.sh and CI fixtures
#      depend on a fully pre-warmed sidecar.
#   3. Set -euo pipefail so any failure aborts immediately.
#
# Env knobs:
#   S2_PORT              -- bind port (default 8765).
#   S2_DEVICE            -- cpu|cuda (default cpu).
#   S2_SSM_CHECKPOINT    -- HF checkpoint id (legacy; default state-spaces/mamba-130m-hf).
#   RC_EMBEDDER          -- embedder backend selection (default codestral-mamba).
#                         Supported: codestral-mamba | mamba-130m | bge-code |
#                         unixcoder-base | random-mamba.
#   BACKGROUND           -- 1 to fork + wait; unset/0 to run in foreground.
#   S2_HEALTH_TIMEOUT    -- seconds to wait for model_loaded:true (default 120).
#   S2_LOG_FILE          -- when BACKGROUND=1, redirect logs here (default /tmp/reasoning-core-sidecar.log).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${S2_PORT:-8765}"
HOST="127.0.0.1"
HEALTH_URL="http://${HOST}:${PORT}/health"
TIMEOUT="${S2_HEALTH_TIMEOUT:-120}"
LOG_FILE="${S2_LOG_FILE:-/tmp/reasoning-core-sidecar.log}"

if [[ -n "${PYTHON:-}" ]]; then                                                                                                                                                       
      PYTHON_BIN="$PYTHON"
  elif [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then                                                                                                                                  
      PYTHON_BIN="${REPO_ROOT}/.venv/bin/python3"                                                                                                                                       
  else
      PYTHON_BIN="python3"                                                                                                                                                              
  fi                                                                                                                                                                                    
   
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ ! -x "$PYTHON_BIN" ]]; then                                                                                                       
      echo "ERROR: $PYTHON_BIN not found" >&2                     
      exit 1                                                                                                                                                                            
  fi                                                              
echo "Using interpreter: $PYTHON_BIN"

# Ensure the project is importable as the `src` package.
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "${BACKGROUND:-0}" == "1" ]]; then
    echo "Starting sidecar in background (logs: $LOG_FILE) ..."
    # Detach via nohup; record the PID for the caller.
    nohup "$PYTHON_BIN" -m src.s2_core >"$LOG_FILE" 2>&1 &
    SIDECAR_PID=$!
    echo "$SIDECAR_PID" > /tmp/reasoning-core-sidecar.pid
    echo "Sidecar PID=$SIDECAR_PID, waiting for /health ..."

    # Poll /health until model_loaded:true or timeout.
    DEADLINE=$(( $(date +%s) + TIMEOUT ))
    while true; do
        if ! kill -0 "$SIDECAR_PID" 2>/dev/null; then
            echo "ERROR: sidecar process exited before becoming healthy. Tail of log:" >&2
            tail -n 50 "$LOG_FILE" >&2 || true
            exit 3
        fi
        BODY="$(curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null || true)"
        if [[ -n "$BODY" ]] && echo "$BODY" | grep -q '"model_loaded":[[:space:]]*true'; then
            echo "Sidecar healthy: $BODY"
            exit 0
        fi
        if (( $(date +%s) >= DEADLINE )); then
            echo "ERROR: sidecar /health did not report model_loaded:true within ${TIMEOUT}s" >&2
            tail -n 50 "$LOG_FILE" >&2 || true
            kill "$SIDECAR_PID" 2>/dev/null || true
            exit 4
        fi
        sleep 1
    done
fi

# Foreground mode: exec replaces this shell so signals propagate cleanly.
exec "$PYTHON_BIN" -m src.s2_core
