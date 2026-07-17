"""Child-env allowlist for sidecar_supervisor (P5 round-2).

Reviewer-flagged: os.environ.copy() leaked operator-posture vars
(RC_ALLOW_GUARD_EDIT, ANTHROPIC_API_KEY, *_TOKEN, *_KEY) into mlx_lm
children. Build child env from explicit allowlist instead.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


_CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR",
    "S2_PORT", "S2_DEVICE", "S2_FAIL_CLOSED", "S2_SSM_CHECKPOINT",
    "S2_MEM_LIMIT_GB", "S2_GEN_MEM_LIMIT_GB", "S2_GEN_MEM_POLL_S",
    "S2_SINGLE_INSTANCE", "S2_GEN_SINGLE_INSTANCE",
    "S2_HARD_CAP_MS", "S2_TIMEOUT", "S2_HEALTH_TIMEOUT", "S2_HEALTH_GRACE_S", "S2_FAILURE_THRESHOLD",
    "S2_LOG_LEVEL", "S2_MEM_LOG_INTERVAL_S", "S2_BACKBONE_FAIL_COOLDOWN_S",
    "S2_MAMBA_STARTUP_GRACE_S",
    "RC_REASONER_BACKEND", "RC_GEN_PORT", "RC_GEN_MODEL",
    "RC_GEN_MODEL_PATH",
    "CLAUDE_PROJECT_DIR",
    "RC_PROJECT_INDEX", "RC_PROJECT_INDEX_MAX", "RC_SESSION_ID",
    "RC_EMBEDDER", "RC_EMBEDDER_DTYPE", "RC_MISTRALAI_MAMBA_CODESTRAL_7B_V0_1_REVISION",
    "RC_GABRIELLARSON_MAMBA_CODESTRAL_7B_V0_1_GGUF_REVISION",
    "RC_CODESTRAL_GGUF_FILE", "RC_GGUF_N_CTX", "RC_GGUF_N_BATCH",
    "RC_GGUF_N_UBATCH", "RC_GGUF_THREADS",
    "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
    "HF_HUB_ENABLE_HF_TRANSFER", "HF_HUB_DISABLE_XET",
    "TRANSFORMERS_OFFLINE", "HF_HUB_OFFLINE",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "PYTORCH_ENABLE_MPS_FALLBACK", "MLX_DEFAULT_DEVICE",
    "VIRTUAL_ENV", "PYTHONPATH",
})


def build_child_env(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env: Dict[str, str] = {
        k: v for k, v in os.environ.items() if k in _CHILD_ENV_ALLOWLIST
    }
    if extra_env:
        env.update(extra_env)
    return env


def gen_extra_env() -> Dict[str, str]:
    extra: Dict[str, str] = {}
    # Ensure the gen launcher module is importable as src.gen_sidecar_launcher
    # even if the parent shell did not set PYTHONPATH.
    repo_root = str(Path(__file__).resolve().parent.parent)
    src_root = str(Path(__file__).resolve().parent)
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in existing.split(":") if p]
    for p in (src_root, repo_root):
        if p not in parts:
            parts.insert(0, p)
    extra["PYTHONPATH"] = ":".join(parts)
    return extra
