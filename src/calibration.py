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
import math
import random
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "src/calibration.py requires numpy. Install it in the venv."
    ) from exc


@dataclass
class CalibrationModel:
    mean: List[float]            # 9-d centroid of benign data
    cov_inv: List[List[float]]   # inverse covariance (regularized)
    threshold: float             # Mahalanobis distance cutoff (FPR target)
    threshold_ci95: Tuple[float, float]
    n: int
    fpr_target: float
    kind: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "CalibrationModel":
        d = json.loads(s)
        d["threshold_ci95"] = tuple(d["threshold_ci95"])
        return cls(**d)


def _regularized_cov(X: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Sample covariance with diagonal regularization. Prevents singular
    covariance when one dim is near-constant on the benign sample."""
    cov = np.cov(X, rowvar=False)
    if cov.ndim == 0:
        cov = np.array([[cov.item()]])
    return cov + eps * np.eye(cov.shape[0])


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


def fit(X_benign: np.ndarray, *, fpr_target: float = 0.02,
        kind: Optional[str] = None) -> CalibrationModel:
    """Fit Mahalanobis calibration to a labeled-benign matrix.

    X_benign: (n_samples, n_dim) — typically n_dim=9 for the risk vector.
    fpr_target: false-positive-rate target for the threshold (per P4
                promotion criterion ≤ 2%).
    """
    if X_benign.ndim != 2:
        raise ValueError(f"X_benign must be 2-D, got shape {X_benign.shape}")
    if X_benign.shape[0] < 10:
        raise ValueError(
            f"need >=10 samples to fit, got {X_benign.shape[0]} "
            f"(per-kind shrinkage handles n<5; refuse n<10 for global fit)"
        )

    mean = X_benign.mean(axis=0)
    cov = _regularized_cov(X_benign)
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


def _james_stein_shrink(per_kind_thr: Dict[str, float],
                        per_kind_n: Dict[str, int],
                        global_thr: float) -> Dict[str, float]:
    """Pull each per-kind threshold toward the global mean inversely with n.

    Standard JS shrinkage on the threshold scalar: τ_kind' = (n*τ_kind +
    α*τ_global) / (n + α). α=5 is the prior weight (anchor at n=5 sample
    equivalent). At n_kind→∞ no shrink; at n_kind=0 fully global.
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
        if Xk.shape[0] < 5:
            # Too sparse — use global model with kind tag
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
            per_kind_n[kind] = int(Xk.shape[0])
            continue
        m = fit(Xk, fpr_target=fpr_target, kind=kind)
        per_kind[kind] = m
        per_kind_thr[kind] = m.threshold
        per_kind_n[kind] = m.n

    shrunk = _james_stein_shrink(per_kind_thr, per_kind_n, global_model.threshold)
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
