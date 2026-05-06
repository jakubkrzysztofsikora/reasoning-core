"""Recalibration watcher (P7 tracker #67).

Polls `eval/runs/recalibrate.signal` (written by `eval.recalibrate` when
Page-Hinkley CUSUM or LOC-churn trips). On presence:
  1. Mine a fresh labeled corpus from git (`eval.calibration_corpus.mine`).
  2. Score every benign row through `s2_core.score_change` to build the
     (n, 8) risk-vector matrix X plus the per-row file_kind list.
  3. Fit per-kind Mahalanobis models and write
     `eval/runs/calibration.json` atomically via `calibration.save_models`.
  4. Unlink the signal so the next tick is idle.

Failures leave the signal in place — next tick retries. The watcher must
honor the supervisor's `stop` Event for clean SIGTERM shutdown (max 60s
default poll, configurable via RC_RECALIBRATE_POLL_S).

NO import-time side effects: nothing here opens files, sockets, or starts
threads at import. Only the public `recalibrate_watcher(stop)` enters a
loop, and only when the supervisor wires it.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple


def _signal_path(repo_root: Path) -> Path:
    return repo_root / "eval" / "runs" / "recalibrate.signal"


def _calibration_path(repo_root: Path) -> Path:
    return repo_root / "eval" / "runs" / "calibration.json"


def _poll_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("RC_RECALIBRATE_POLL_S", "60")))
    except ValueError:
        return 60.0


def _read_signal(path: Path) -> Optional[dict]:
    """Read + parse signal JSON. Returns None on missing/corrupt content."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write(
            f"[supervisor:recalibrate] signal at {path} is corrupt; "
            f"leaving in place\n"
        )
        return {"_corrupt": True}
    return data if isinstance(data, dict) else {"_corrupt": True}


def _build_xy(repo_root: Path, *,
              mine_fn: Optional[Callable] = None,
              score_fn: Optional[Callable] = None
              ) -> Tuple[List[List[float]], List[str]]:
    """Mine labeled benign rows + score them through s2_core.

    Injectable for tests:
      mine_fn(repo, since, out) -> counts dict; writes JSONL at out.
      score_fn(path, before, after) -> object with .risk_vector list[float].
    """
    if mine_fn is None:
        from eval.calibration_corpus import mine as mine_fn  # type: ignore
    if score_fn is None:
        from s2_core import score_change as score_fn  # type: ignore

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        out_path = Path(tf.name)
    try:
        mine_fn(str(repo_root), "6.months", str(out_path))
        X: List[List[float]] = []
        kinds: List[str] = []
        with open(out_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if row.get("label") != "negative":
                    continue
                try:
                    report = score_fn(
                        row.get("path", ""),
                        row.get("before", ""),
                        row.get("after", ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"[supervisor:recalibrate] score_change failed for "
                        f"{row.get('path','?')}: {exc}\n"
                    )
                    continue
                vec = list(getattr(report, "risk_vector", []) or [])
                if not vec:
                    continue
                X.append([float(v) for v in vec])
                kinds.append(str(row.get("file_kind") or "source_code"))
        return X, kinds
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def refit_once(repo_root: Path, *,
               mine_fn: Optional[Callable] = None,
               score_fn: Optional[Callable] = None,
               fit_fn: Optional[Callable] = None,
               save_fn: Optional[Callable] = None) -> bool:
    """Run one refit cycle. Returns True iff models were saved successfully.

    All collaborators are injectable so tests can drive the pipeline without
    git, the Mamba backbone, or numpy when undesired.
    """
    if fit_fn is None:
        from calibration import fit_per_kind as fit_fn  # type: ignore
    if save_fn is None:
        from calibration import save_models as save_fn  # type: ignore

    X, kinds = _build_xy(repo_root, mine_fn=mine_fn, score_fn=score_fn)
    if not X:
        sys.stderr.write(
            "[supervisor:recalibrate] no benign rows scored; aborting refit\n"
        )
        return False
    try:
        import numpy as np  # local import to keep module import-light
        X_np = np.asarray(X, dtype=float)
    except ImportError as exc:
        sys.stderr.write(f"[supervisor:recalibrate] numpy missing: {exc}\n")
        return False

    models = fit_fn(X_np, kinds)
    save_fn(models, _calibration_path(repo_root))
    return True


def _tick(repo_root: Path, *, mine_fn=None, score_fn=None,
          fit_fn=None, save_fn=None) -> None:
    sig_path = _signal_path(repo_root)
    if not sig_path.exists():
        return
    payload = _read_signal(sig_path)
    if payload is None:
        return  # transient OS error; retry next tick
    if payload.get("_corrupt"):
        return  # leave signal so operator can inspect / replace
    reasons = payload.get("reasons") or []
    sys.stderr.write(
        f"[supervisor:recalibrate] signal observed; reasons={reasons}\n"
    )
    try:
        ok = refit_once(repo_root, mine_fn=mine_fn, score_fn=score_fn,
                        fit_fn=fit_fn, save_fn=save_fn)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"[supervisor:recalibrate] refit raised {type(exc).__name__}: "
            f"{exc}; leaving signal in place\n"
        )
        return
    if ok:
        try:
            sig_path.unlink(missing_ok=True)
        except OSError as exc:
            sys.stderr.write(
                f"[supervisor:recalibrate] unlink signal failed: {exc}\n"
            )
        else:
            sys.stderr.write(
                "[supervisor:recalibrate] refit complete; signal cleared\n"
            )


def recalibrate_watcher(stop: threading.Event, repo_root: Path, *,
                        poll_s: Optional[float] = None,
                        mine_fn=None, score_fn=None,
                        fit_fn=None, save_fn=None) -> None:
    """Daemon-thread loop. Wake on `stop` for prompt SIGTERM exit."""
    interval = poll_s if poll_s is not None else _poll_seconds()
    while not stop.is_set():
        try:
            _tick(repo_root, mine_fn=mine_fn, score_fn=score_fn,
                  fit_fn=fit_fn, save_fn=save_fn)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"[supervisor:recalibrate] tick error: {exc}\n"
            )
        stop.wait(interval)
