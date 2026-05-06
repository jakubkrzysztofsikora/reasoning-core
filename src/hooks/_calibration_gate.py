"""Mahalanobis-calibration gate for pre_edit_guard (P7 tracker #61).

Wires `src/calibration.py` (CalibrationModel.from_json + load_models +
decide) into the hook's risk-vector evaluation path. Default OFF — only
activates when `RC_CALIBRATION_ENABLED=1` AND
`eval/runs/calibration.json` (or `RC_CALIBRATION_PATH`) exists.

Per-kind dispatch: each scored edit's `file_kind` (from the sidecar
report — "source_code", "test_code", "plan", etc.) selects the matching
per-kind CalibrationModel. Falls back to a "_global" / "default" model
when present, else to the first model in the file.

Cache keyed by file mtime so the supervisor's auto-refit is picked up
without restarting the Claude Code session.

Failure semantics:
  - missing file       → silently disabled, log once via stderr
  - corrupt JSON       → silently disabled, log once via stderr
  - numpy unavailable  → silently disabled, log once via stderr
  - per-kind miss      → fall back to "_global"/"default" or first model
                         (still scored, just less specific)

The gate NEVER hard-blocks on its own; it returns an advisory anomaly
flag + score. The caller (pre_edit_guard) decides whether to enforce
under existing RC_SHADOW_MODE posture.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Round-3 fix (AH-H1): pre-warm numpy + calibration at import time when the
# gate is enabled, so the FIRST hot-path call doesn't pay 80-300ms of cold
# import. Best-effort — if either fails, lazy-import inside evaluate() still
# works (warn-once preserves silent-disable contract).
_NP = None
_CALIBRATION = None
if os.environ.get("RC_CALIBRATION_ENABLED") == "1":
    try:
        import numpy as _np  # type: ignore
        _NP = _np
    except ImportError:
        pass
    _SRC = Path(__file__).resolve().parent.parent
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    try:
        import calibration as _calibration_mod  # type: ignore
        _CALIBRATION = _calibration_mod
    except ImportError:
        pass

# Module-level cache: (path_str, mtime_ns) -> {kind: CalibrationModel}
_MODEL_CACHE: Dict[Tuple[str, int], Dict[str, Any]] = {}
_WARNED_PATHS: set = set()


def is_enabled() -> bool:
    return os.environ.get("RC_CALIBRATION_ENABLED") == "1"


def _calibration_path() -> Path:
    override = os.environ.get("RC_CALIBRATION_PATH")
    if override:
        return Path(override)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(project_dir) / "eval" / "runs" / "calibration.json"


def _warn_once(key: str, msg: str) -> None:
    if key in _WARNED_PATHS:
        return
    _WARNED_PATHS.add(key)
    try:
        sys.stderr.write(f"[hybrid-reasoner] calibration disabled: {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _load_models_cached(path: Path) -> Optional[Dict[str, Any]]:
    """Lazy-load + cache models keyed by mtime. Returns None on any error."""
    try:
        if not path.exists() or not path.is_file():
            _warn_once(f"missing:{path}", f"file not found ({path})")
            return None
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        _warn_once(f"stat:{path}", f"stat failed: {exc}")
        return None

    cache_key = (str(path), mtime_ns)
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Lazy-import calibration so the hook keeps working when numpy/calibration
    # are unavailable in the venv. The src/ dir must be on sys.path.
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        src_dir = str(repo_root / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import calibration as _calibration  # type: ignore
    except ImportError as exc:
        _warn_once(f"import:{path}", f"calibration import failed: {exc}")
        return None

    try:
        models = _calibration.load_models(path)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        _warn_once(f"load:{path}:{mtime_ns}", f"corrupt model file: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"load:{path}:{mtime_ns}", f"load failed: {exc}")
        return None

    # Drop stale cache entries for this path (different mtime).
    for stale in [k for k in _MODEL_CACHE if k[0] == str(path) and k != cache_key]:
        _MODEL_CACHE.pop(stale, None)
    _MODEL_CACHE[cache_key] = models
    return models


def _select_model(models: Dict[str, Any], kind: Optional[str]) -> Optional[Any]:
    if not models:
        return None
    if kind and kind in models:
        return models[kind]
    for fallback in ("_global", "default", "global"):
        if fallback in models:
            return models[fallback]
    # Last resort: first model in deterministic order.
    try:
        first_key = sorted(models.keys())[0]
        return models[first_key]
    except (IndexError, TypeError):
        return None


def evaluate(
    risk_vector: Sequence[float],
    *,
    file_kind: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Score the given risk_vector through the calibration model.

    Returns None when calibration is disabled, model is missing/corrupt,
    or the risk_vector is malformed. Otherwise returns a dict with:

        {
          "anomaly": bool,            # decide() result
          "score": float,             # squared Mahalanobis distance
          "threshold": float,
          "kind_used": str,           # which model key was selected
          "model_n": int,             # n samples backing the model
        }
    """
    if not is_enabled():
        return None
    if not risk_vector or not isinstance(risk_vector, (list, tuple)):
        return None

    path = _calibration_path()
    models = _load_models_cached(path)
    if not models:
        return None

    model = _select_model(models, file_kind)
    if model is None:
        return None

    try:
        import numpy as np  # type: ignore
        import calibration as _calibration  # type: ignore
    except ImportError as exc:
        _warn_once("numpy", f"numpy/calibration unavailable: {exc}")
        return None

    try:
        x = np.array([float(v) for v in risk_vector], dtype=float)
    except (TypeError, ValueError):
        return None

    # Dimension mismatch — model was fit on different vector width. Skip.
    if x.shape[0] != len(model.mean):
        _warn_once(
            f"dim:{path}:{x.shape[0]}vs{len(model.mean)}",
            f"risk_vector dim {x.shape[0]} != model dim {len(model.mean)}",
        )
        return None

    try:
        score_val = _calibration.score(model, x)
        anomaly = _calibration.decide(model, x)
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"score:{path}", f"score failed: {exc}")
        return None

    # Resolve which key was actually used for transparency.
    kind_used = file_kind if (file_kind and file_kind in models) else (
        next((k for k in ("_global", "default", "global") if k in models),
             sorted(models.keys())[0] if models else "")
    )

    return {
        "anomaly": bool(anomaly),
        "score": float(score_val),
        "threshold": float(model.threshold),
        "kind_used": kind_used,
        "model_n": int(getattr(model, "n", 0)),
    }


def reset_cache_for_tests() -> None:
    """Test hook — clears the module-level cache + warning de-dup set."""
    _MODEL_CACHE.clear()
    _WARNED_PATHS.clear()
