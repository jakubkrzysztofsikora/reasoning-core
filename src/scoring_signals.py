"""Post-embedding anomaly signals -- Phase C (audit-deferred).

The 2026-09-19 audit proved that AIS, coherence_delta, and novelty are
algebraically redundant (all scalar transforms of cosine similarity).
The scoring-v3 memo's recommendation is to keep ``coherence_delta`` as
the canonical 0..2 readout and add a *new, independent* anomaly signal
whose value is NOT derivable from cos. This module provides that
signal.

We expose three primitives (all pure numpy, no sklearn, no torch):

* ``fit_benign_corpus(embs)`` -- Ledoit-Wolf shrunk inverse covariance
  over a benign-embedding corpus. Returns the centroid and the inverse
  covariance.
* ``mahal_anomaly_against_corpus(emb, mean, cov_inv)`` -- squared
  Mahalanobis distance of a single embedding against the fitted
  corpus. Returns 0.0 at the centroid, grows with distance, and
  returns +inf if ``cov_inv`` is degenerate (zero matrix).
* ``threshold_for_fpr(distances, fpr)`` -- quantile threshold that
  yields the requested false-positive rate on the benign distances.

The math is delegated to ``src.calibration`` (Ledoit-Wolf + squared
Mahalanobis). This module only adds:

* a friendly public API that takes/returns numpy arrays directly,
* the inf-on-degenerate convention so callers can detect a broken
  calibration without an exception,
* a tiny ``threshold_for_fpr`` helper that the recalibration step
  uses to pick a per-file-kind operating point.

The module is wired into ``src.s2_core.score_change`` behind the
``RC_SCORING_V3=1`` env flag. When the flag is off, the call path
is untouched and ``mahal_anomaly`` stays ``None`` on the report.

Reference:
  - thoughts/shared/research/2026-09-19-audit-deferred-scoring-v3.md
  - thoughts/shared/plans/2026-09-19-audit-deferred-rollup.md (Phase C)
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

try:
    # Reuse the existing Ledoit-Wolf implementation. We deliberately
    # import the private helpers because the algorithm details
    # (shrinkage target, lambda cap) are not part of the public API
    # of src.calibration yet.
    from src.calibration import _ledoit_wolf_cov, _mahalanobis_sq  # noqa: F401
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "src.scoring_signals requires src.calibration. Install numpy."
    ) from exc


__all__ = [
    "fit_benign_corpus",
    "mahal_anomaly_against_corpus",
    "threshold_for_fpr",
]


def fit_benign_corpus(
    embs: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Fit a Ledoit-Wolf shrunk covariance on benign embeddings.

    Parameters
    ----------
    embs
        ``(n, d)`` array of benign embeddings with ``n >= 1``.

    Returns
    -------
    mean
        ``(d,)`` centroid.
    cov_inv
        ``(d, d)`` inverse covariance, regularized by Ledoit-Wolf
        shrinkage so it is always positive-definite.

    Raises
    ------
    ValueError
        If ``embs`` is empty or 1-D.
    """
    arr = np.asarray(embs, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(
            f"embs must be 2-D (n_samples, dim); got shape {arr.shape!r}"
        )
    if arr.shape[0] < 1:
        raise ValueError(
            f"embs must contain at least one sample; got {arr.shape[0]}"
        )
    if arr.shape[1] < 1:
        raise ValueError("embs must have at least one dimension")
    mean = arr.mean(axis=0)
    cov = _ledoit_wolf_cov(arr)
    # Symmetrize to wash out tiny floating-point asymmetry from
    # Ledoit-Wolf's outer-product updates before inverting.
    cov_sym = 0.5 * (cov + cov.T)
    try:
        cov_inv = np.linalg.inv(cov_sym)
    except np.linalg.LinAlgError:
        # Degenerate (e.g. all-zero input). Surface as zero inverse
        # so callers see ``inf`` from mahal_anomaly_against_corpus.
        cov_inv = np.zeros_like(cov_sym)
    return mean, cov_inv


def mahal_anomaly_against_corpus(
    emb: np.ndarray,
    mean: np.ndarray,
    cov_inv: np.ndarray,
) -> float:
    """Squared Mahalanobis distance of ``emb`` against the benign corpus.

    Returns
    -------
    float
        Non-negative score. Zero at the centroid, growing with the
        distance from the centroid. ``+inf`` is returned when
        ``cov_inv`` is degenerate (all-zero), so callers can detect
        a broken calibration cleanly.
    """
    delta = np.asarray(emb, dtype=np.float64) - np.asarray(mean, dtype=np.float64)
    inv = np.asarray(cov_inv, dtype=np.float64)
    # Degenerate inverse: every distance is mathematically undefined;
    # surface +inf so the caller's threshold check is a clean miss
    # rather than a silent zero.
    if inv.shape == (0, 0) or (inv == 0).all():
        return float("inf")
    return float(_mahalanobis_sq(delta, np.zeros_like(delta), inv))


def threshold_for_fpr(distances: np.ndarray, fpr: float) -> float:
    """Pick the threshold that yields the requested FPR on benign distances.

    The threshold is the ``(1 - fpr)`` quantile of the squared
    distances. With Ledoit-Wolf shrinkage this is stable for ``n >= 5``
    per the calibration success criterion.

    Parameters
    ----------
    distances
        1-D array of benign squared Mahalanobis distances.
    fpr
        Target false-positive rate in (0, 1).

    Returns
    -------
    float
        Threshold value. Returns 0.0 if ``distances`` is empty so the
        caller falls through to a safe default.

    Raises
    ------
    ValueError
        If ``fpr`` is outside (0, 1).
    """
    if not (0.0 < fpr < 1.0):
        raise ValueError(f"fpr must be in (0, 1); got {fpr!r}")
    arr = np.asarray(distances, dtype=np.float64).ravel()
    if arr.size == 0:
        return 0.0
    return float(np.quantile(arr, 1.0 - fpr))
