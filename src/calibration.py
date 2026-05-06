"""Mahalanobis-distance calibration over 9-dim risk space (P7).

Replaces v1's "any-dim > 0.9" OR rule (effective FPR ~22.6% at k_eff≈5)
with a single Mahalanobis distance threshold fit on labeled-benign data.

Hierarchical Bayes per-kind shrinkage (James-Stein style): per-file-kind
thresholds are pulled toward the global mean inversely proportional to
per-kind sample count. Stable at n_kind ≥ 5 (success criterion).

Bootstrap 95% CI on each threshold (B=1000); report width so callers
can see how loaded the threshold is.

Public API:
    fit(X_benign: np.ndarray) -> CalibrationModel
    fit_per_kind(X_benign: np.ndarray, kinds: list[str]) -> dict[str, CalibrationModel]
    score(model, x: np.ndarray) -> float       # Mahalanobis distance
    decide(model, x: np.ndarray) -> bool        # True == anomaly (above thr)

Dependencies: numpy only. No sklearn — keeps the hook-side import light
and predictable across Python versions.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "src/calibration.py requires numpy. Install it in the venv."
    ) from exc


@dataclass
class CalibrationModel:
    mean: List[float]            # centroid of benign data
    cov_inv: List[List[float]]   # inverse covariance (regularized)
    threshold: float             # Mahalanobis distance cutoff (FPR target)
    threshold_ci95: Tuple[float, float]
    n: int
    fpr_target: float
    kind: Optional[str] = None

    @property
    def threshold_ci_width(self) -> float:
        """Bootstrap CI width (round-2 fix: surfaced as explicit field)."""
        return float(self.threshold_ci95[1] - self.threshold_ci95[0])

    def to_json(self) -> str:
        d = asdict(self)
        d["threshold_ci_width"] = self.threshold_ci_width
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str) -> "CalibrationModel":
        d = json.loads(s)
        d.pop("threshold_ci_width", None)  # derived field
        required = {"mean", "cov_inv", "threshold", "threshold_ci95", "n", "fpr_target"}
        missing = required - d.keys()
        if missing:
            raise ValueError(f"calibration json missing keys: {sorted(missing)}")
        d["threshold_ci95"] = tuple(d["threshold_ci95"])
        return cls(**d)


def save_models(per_kind: Dict[str, CalibrationModel], path: Path) -> None:
    """Persistence contract for `fit_per_kind` output.

    **Atomic write** (round-3 round-2 senior-dev C1): the hot-path reader
    in `src/hooks/_calibration_gate.py` keys its mtime cache on
    `(path, mtime_ns)` and disables itself with warn-once on JSONDecodeError.
    A non-atomic `path.write_text` could yield a torn file if a hook reads
    between the truncate and the full write — durably disabling the gate
    until the supervisor refits again.
    Use `tmp + os.replace` (POSIX atomic rename): readers always see either
    the old file (with old mtime) or the new file (with new mtime), never
    a half-written one.
    """
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {kind: json.loads(m.to_json()) for kind, m in per_kind.items()}
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, path)
    except Exception:
        # Cleanup orphan tmp on any failure (write_text or replace).
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_models(path: Path) -> Dict[str, CalibrationModel]:
    raw = json.loads(path.read_text())
    return {kind: CalibrationModel.from_json(json.dumps(d)) for kind, d in raw.items()}


def _ledoit_wolf_cov(X: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf 2004 shrinkage estimator. Shrinks sample covariance
    toward (tr(S)/d) * I with optimal lambda. Pure numpy.

    Round-2 fix: replaces fixed `1e-4 * I` diagonal loading with the
    closed-form analytic shrinkage — better behavior on near-singular
    sample covariances and small n.
    """
    n, d = X.shape
    Xc = X - X.mean(axis=0)
    S = (Xc.T @ Xc) / max(n, 1)
    mu = float(np.trace(S) / d)
    F = mu * np.eye(d)
    # Frobenius squared distance numerator
    diff_sq = float(np.sum((S - F) ** 2))
    # Variance of sample-cov entries (sum_i ||x_i x_i^T - S||_F^2 / n^2)
    var_sum = 0.0
    for i in range(n):
        outer = np.outer(Xc[i], Xc[i])
        var_sum += float(np.sum((outer - S) ** 2))
    var_sum /= max(n * n, 1)
    if diff_sq <= 0:
        lam = 1.0
    else:
        lam = max(0.0, min(1.0, var_sum / diff_sq))
    return (1.0 - lam) * S + lam * F


def _mahalanobis_sq(x: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray) -> float:
    delta = x - mean
    return float(delta @ cov_inv @ delta)


def _threshold_at_fpr(distances_sq: np.ndarray, fpr: float) -> float:
    """The (1-fpr) quantile of squared Mahalanobis distances."""
    if distances_sq.size == 0:
        return 0.0
    return float(np.quantile(distances_sq, 1.0 - fpr))


def _bootstrap_threshold_ci(distances_sq: np.ndarray, fpr: float, *,
                            n_boot: int = 1000,
                            seed: int = 11) -> Tuple[float, float]:
    rng = random.Random(seed)
    n = distances_sq.size
    if n == 0:
        return (0.0, 0.0)
    samples: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        samples.append(_threshold_at_fpr(distances_sq[idx], fpr))
    samples.sort()
    lo = samples[max(0, int(0.025 * n_boot))]
    hi = samples[min(n_boot - 1, int(0.975 * n_boot))]
    return (lo, hi)


_MIN_GLOBAL_SAMPLES = 50  # n >= ~5*dim for 9-d covariance estimation


def fit(X_benign: np.ndarray, *, fpr_target: float = 0.02,
        kind: Optional[str] = None) -> CalibrationModel:
    """Fit Mahalanobis calibration to a labeled-benign matrix.

    X_benign: (n_samples, n_dim) — typically n_dim=9 for the risk vector.
    fpr_target: false-positive-rate target for the threshold (per P4
                promotion criterion ≤ 2%).

    Round-2 fix: bumped n>=10 → n>=50 (rule of thumb 5*dim for 9-d cov).
    Sparse per-kind fits (n<50) fall back to global model in `fit_per_kind`.
    """
    if X_benign.ndim != 2:
        raise ValueError(f"X_benign must be 2-D, got shape {X_benign.shape}")
    if X_benign.shape[0] < _MIN_GLOBAL_SAMPLES:
        raise ValueError(
            f"need >={_MIN_GLOBAL_SAMPLES} samples to fit (5x dim rule of "
            f"thumb for {X_benign.shape[1]}-d covariance), "
            f"got {X_benign.shape[0]}"
        )

    mean = X_benign.mean(axis=0)
    cov = _ledoit_wolf_cov(X_benign)
    cov_inv = np.linalg.inv(cov)

    distances_sq = np.array([
        _mahalanobis_sq(X_benign[i], mean, cov_inv)
        for i in range(X_benign.shape[0])
    ])
    thr = _threshold_at_fpr(distances_sq, fpr_target)
    ci = _bootstrap_threshold_ci(distances_sq, fpr_target)

    return CalibrationModel(
        mean=mean.tolist(),
        cov_inv=cov_inv.tolist(),
        threshold=thr,
        threshold_ci95=ci,
        n=X_benign.shape[0],
        fpr_target=fpr_target,
        kind=kind,
    )


def _empirical_bayes_shrink(per_kind_thr: Dict[str, float],
                             per_kind_n: Dict[str, int],
                             global_thr: float) -> Dict[str, float]:
    """Conjugate-prior posterior mean shrinkage of per-kind thresholds.

    τ_kind' = (n*τ_kind + α*τ_global) / (n + α). α=5 is the prior weight
    (anchor at n=5 sample equivalent). At n_kind→∞ no shrink; at n=0 fully
    global.

    Round-2 fix: renamed from _james_stein_shrink — the textbook JS estimator
    requires d≥3 vector means with known variance and has risk-dominance;
    this is just a conjugate Bayesian posterior on a scalar with no JS
    guarantee. Behavior unchanged, label corrected.
    """
    alpha = 5.0
    return {
        k: (per_kind_n.get(k, 0) * per_kind_thr[k] + alpha * global_thr) /
           (per_kind_n.get(k, 0) + alpha)
        for k in per_kind_thr
    }


def fit_per_kind(X_benign: np.ndarray, kinds: Sequence[str], *,
                 fpr_target: float = 0.02) -> Dict[str, CalibrationModel]:
    """Per-file-kind models with James-Stein shrinkage on the threshold.

    Returns one CalibrationModel per kind. Each model uses its own per-kind
    centroid + cov_inv (no shrinkage on those — the centroid IS the kind),
    but the threshold is pulled toward the global threshold inversely with n.
    """
    if len(kinds) != X_benign.shape[0]:
        raise ValueError("kinds length must match X_benign rows")
    global_model = fit(X_benign, fpr_target=fpr_target)

    unique = sorted(set(kinds))
    per_kind: Dict[str, CalibrationModel] = {}
    per_kind_thr: Dict[str, float] = {}
    per_kind_n: Dict[str, int] = {}

    for kind in unique:
        mask = np.array([k == kind for k in kinds])
        Xk = X_benign[mask]
        if Xk.shape[0] < _MIN_GLOBAL_SAMPLES:
            # Too sparse — fully global. Set per_kind_n=0 so shrinkage
            # collapses to global cleanly (round-2 fix: previous impl used
            # int(Xk.shape[0]) which polluted the conjugate-prior weight).
            per_kind[kind] = CalibrationModel(
                mean=global_model.mean,
                cov_inv=global_model.cov_inv,
                threshold=global_model.threshold,
                threshold_ci95=global_model.threshold_ci95,
                n=int(Xk.shape[0]),
                fpr_target=fpr_target,
                kind=kind,
            )
            per_kind_thr[kind] = global_model.threshold
            per_kind_n[kind] = 0
            continue
        m = fit(Xk, fpr_target=fpr_target, kind=kind)
        per_kind[kind] = m
        per_kind_thr[kind] = m.threshold
        per_kind_n[kind] = m.n

    shrunk = _empirical_bayes_shrink(per_kind_thr, per_kind_n, global_model.threshold)
    for kind, thr in shrunk.items():
        prior = per_kind[kind]
        per_kind[kind] = CalibrationModel(
            mean=prior.mean,
            cov_inv=prior.cov_inv,
            threshold=thr,
            threshold_ci95=prior.threshold_ci95,
            n=prior.n,
            fpr_target=prior.fpr_target,
            kind=kind,
        )
    return per_kind


def score(model: CalibrationModel, x: np.ndarray) -> float:
    """Squared Mahalanobis distance from x to the model centroid."""
    mean = np.array(model.mean)
    cov_inv = np.array(model.cov_inv)
    return _mahalanobis_sq(x, mean, cov_inv)


def decide(model: CalibrationModel, x: np.ndarray) -> bool:
    """True when x is above the FPR-calibrated threshold (anomaly)."""
    return score(model, x) > model.threshold
