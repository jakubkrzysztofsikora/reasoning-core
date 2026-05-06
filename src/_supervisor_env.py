"""Child-env allowlist for sidecar_supervisor (P5 round-2).

Reviewer-flagged: os.environ.copy() leaked operator-posture vars
(RC_ALLOW_GUARD_EDIT, ANTHROPIC_API_KEY, *_TOKEN, *_KEY) into mlx_lm
children. Build child env from explicit allowlist instead.
"""
from __future__ import annotations

import os
from typing import Dict, Optional


_CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR",
    "S2_PORT", "S2_FAIL_CLOSED",
    "RC_REASONER_BACKEND", "RC_GEN_PORT", "RC_GEN_MODEL",
    "RC_GEN_MODEL_PATH", "RC_GEN_LOG", "RC_GEN_URL",
    "CLAUDE_PROJECT_DIR",
    "HF_HOME", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
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
    if "MLX_DEFAULT_DEVICE" not in os.environ:
        extra["MLX_DEFAULT_DEVICE"] = "cpu"
    return extra
