"""Post-embedding anomaly signals -- Phase C (audit-deferred).

The 2026-09-19 audit proved that AIS, coherence_delta, and novelty are
algebraically redundant (all scalar transforms of cosine similarity).
The scoring-v3 memo's recommendation is to keep ``coherence_delta`` as
the canonical 0..2 readout and add a *new, independent* anomaly signal
whose value is NOT derivable from cos. This module provides that
signal.

We expose three primitives (all pure numpy, no sklearn, no torch):

* ``fit_benign_corpus(embs)`` -- Ledoit-Wolf shrunk covariance over a
  benign-embedding corpus. Returns ``(mean, cov, cov_inv)`` where ``cov``
  is the symmetric shrunk covariance matrix (for condition-number checks)
  and ``cov_inv`` is its inverse.
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
    "loo_threshold_for_fpr",
]


def fit_benign_corpus(
    embs: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit a Ledoit-Wolf shrunk covariance on benign embeddings.

    Parameters
    ----------
    embs
        ``(n, d)`` array of benign embeddings with ``n >= 1``.

    Returns
    -------
    mean
        ``(d,)`` centroid.
    cov
        ``(d, d)`` shrunk covariance matrix (symmetric, positive-semidefinite).
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
    return mean, cov_sym, cov_inv


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

    NOTE: this is an IN-SAMPLE quantile -- the FPR is calibrated and
    evaluated on the same corpus. Use ``loo_threshold_for_fpr`` for an
    honest out-of-sample threshold when the corpus is small. The
    production scoring path (s2_core._maybe_promote_session_to_corpus)
    uses ``loo_threshold_for_fpr`` for this reason; this function
    remains for tests that explicitly want the in-sample estimate.
    See 2026-09-22 hostile review Finding 3 for the rationale.

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


def loo_threshold_for_fpr(
    benign_embs: np.ndarray,
    fpr: float,
) -> float:
    """Honest out-of-sample FPR threshold via leave-one-out (LOO).

    For each row in ``benign_embs`` we (1) fit Ledoit-Wolf on the
    other n-1 rows, (2) score the held-out row against the fitted
    inverse, (3) accumulate the LOO distance. The threshold is then
    the ``(1 - fpr)`` quantile of the LOO distances.

    Honest contract (round-3 retest): the realized power of this
    detector at small corpus sizes is much lower than the nominal FPR
    implies. With n=5 the LOO is nearly inert (~0% power against
    fresh edits). The detector should be treated as ADVISORY at
    small corpus sizes, not as a hard gate. As the corpus grows the
    LOO power approaches the nominal FPR, but the round-3 review
    confirmed that the n=5 path does NOT actually realize the 0.05
    FPR the previous docstring claimed. The honest contract is: at
    small n, treat the detector as advisory; at large n, the
    nominal FPR is the calibration target.

    Parameters
    ----------
    benign_embs
        ``(n, d)`` array of benign embeddings, ``n >= 2``. The function
        returns 0.0 for n < 2 so callers fall through to a safe default.
    fpr
        Target false-positive rate in (0, 1).

    Returns
    -------
    float
        LOO threshold value. Returns 0.0 if ``n < 2`` or the corpus
        is degenerate.

    Raises
    ------
    ValueError
        If ``fpr`` is outside (0, 1) or ``benign_embs`` is not 2-D.
    """
    if not (0.0 < fpr < 1.0):
        raise ValueError(f"fpr must be in (0, 1); got {fpr!r}")
    arr = np.asarray(benign_embs, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(
            f"benign_embs must be 2-D (n_samples, dim); got shape {arr.shape!r}"
        )
    n = arr.shape[0]
    if n < 2:
        return 0.0
    loo_dists = np.empty(n, dtype=np.float64)
    for i in range(n):
        # Fit on all rows except i.
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        train = arr[mask]
        if train.shape[0] < 2:
            loo_dists[i] = 0.0
            continue
        try:
            m, _cov, inv = fit_benign_corpus(train)
        except ValueError:
            loo_dists[i] = 0.0
            continue
        if (inv == 0).all():
            loo_dists[i] = float("inf")
            continue
        loo_dists[i] = mahal_anomaly_against_corpus(arr[i], m, inv)
    finite = loo_dists[np.isfinite(loo_dists)]
    if finite.size == 0:
        return 0.0
    return float(np.quantile(finite, 1.0 - fpr))
