#!/usr/bin/env bash
# Boot the generative critic sidecar (P5).
#
# Selects backend via RC_REASONER_BACKEND:
#   mlx    — Apple Silicon (default on macOS); mlx_lm.server with Qwen2.5-Coder-1.5B-Instruct-4bit
#   llama  — Linux / Intel Mac / CI; llama-cpp-python server with GGUF Q4_K_M
#   remote — HTTP client to a hosted endpoint (no local sidecar to start)
#
# Reviewer corrections folded in:
#   - Linux fallback path mandatory (CI reproducibility)
#   - Symmetric /health endpoint
#   - launchd KeepAlive handled by sidecar_supervisor.py (P5 follow-up)
#   - Single broker not two independent servers (deferred to supervisor)

set -euo pipefail

PORT="${RC_GEN_PORT:-8766}"
BACKEND="${RC_REASONER_BACKEND:-mlx}"
LOG_FILE="${RC_GEN_LOG:-/tmp/reasoning-core-gen-sidecar.log}"

case "$BACKEND" in
  mlx)
    if ! command -v mlx_lm.server >/dev/null 2>&1; then
      echo "ERROR: mlx_lm.server not on PATH. pip install mlx-lm." >&2
      exit 1
    fi
    MODEL="${RC_GEN_MODEL:-mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit}"
    echo "[gen-sidecar] starting mlx_lm.server: model=$MODEL port=$PORT" >&2
    exec mlx_lm.server --model "$MODEL" --port "$PORT" >>"$LOG_FILE" 2>&1
    ;;
  llama)
    if ! python3 -c "import llama_cpp" 2>/dev/null; then
      echo "ERROR: llama-cpp-python not installed. pip install 'llama-cpp-python[server]'." >&2
      exit 1
    fi
    MODEL_PATH="${RC_GEN_MODEL_PATH:-$HOME/.cache/reasoning-core/qwen2.5-coder-1.5b-instruct.Q4_K_M.gguf}"
    if [[ ! -f "$MODEL_PATH" ]]; then
      echo "ERROR: GGUF model not found at $MODEL_PATH" >&2
      echo "Download with:" >&2
      echo "  hf download bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF \\" >&2
      echo "    Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf --local-dir \"$(dirname $MODEL_PATH)\"" >&2
      exit 1
    fi
    echo "[gen-sidecar] starting llama-cpp-python: model=$MODEL_PATH port=$PORT" >&2
    exec python3 -m llama_cpp.server --model "$MODEL_PATH" --port "$PORT" >>"$LOG_FILE" 2>&1
    ;;
  remote)
    echo "[gen-sidecar] backend=remote — no local sidecar to start." >&2
    echo "[gen-sidecar] RC_GEN_URL=${RC_GEN_URL:-not set}" >&2
    exit 0
    ;;
  *)
    echo "ERROR: unknown RC_REASONER_BACKEND='$BACKEND'. Expected mlx|llama|remote." >&2
    exit 1
    ;;
esac
