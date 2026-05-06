"""Tests for src/calibration.py — Mahalanobis + James-Stein shrinkage."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

np = pytest.importorskip("numpy")
import calibration  # type: ignore  # noqa: E402


def _make_benign(n: int, dim: int = 9, seed: int = 0) -> "np.ndarray":
    rng = np.random.default_rng(seed)
    return rng.normal(0.0, 1.0, size=(n, dim))


def test_fit_basic():
    X = _make_benign(200)
    m = calibration.fit(X, fpr_target=0.05)
    assert len(m.mean) == 9
    assert m.threshold > 0
    assert m.threshold_ci95[0] <= m.threshold <= m.threshold_ci95[1]
    assert m.n == 200


def test_score_at_centroid_is_zero():
    X = _make_benign(200)
    m = calibration.fit(X)
    centroid = np.array(m.mean)
    assert calibration.score(m, centroid) < 1e-6


def test_decide_flags_far_outliers():
    X = _make_benign(500)
    m = calibration.fit(X, fpr_target=0.02)
    # Point far outside the benign cloud → anomaly
    far = np.array(m.mean) + 100.0
    assert calibration.decide(m, far) is True
    # Centroid → not anomaly
    assert calibration.decide(m, np.array(m.mean)) is False


def test_fpr_calibration_holds_on_held_out():
    """Threshold fit at target FPR should hold approximately on a held-out
    benign sample drawn from the same distribution."""
    fit_X = _make_benign(500, seed=1)
    test_X = _make_benign(2000, seed=2)
    m = calibration.fit(fit_X, fpr_target=0.02)
    fpr = np.mean([calibration.decide(m, x) for x in test_X])
    # Within ±2% of the 2% target on n=2000 (sampling noise)
    assert fpr < 0.06, f"held-out FPR {fpr:.3f} too high vs target 0.02"


def test_fit_rejects_too_few_samples():
    with pytest.raises(ValueError):
        calibration.fit(_make_benign(5))


def test_fit_per_kind_shrinks_sparse_kinds():
    rng = np.random.default_rng(3)
    n_dense, n_sparse = 200, 4  # sparse < 5 → falls back to global
    X_dense = rng.normal(0, 1, size=(n_dense, 9))
    X_sparse = rng.normal(0, 1, size=(n_sparse, 9))
    X = np.vstack([X_dense, X_sparse])
    kinds = ["source"] * n_dense + ["plan"] * n_sparse
    models = calibration.fit_per_kind(X, kinds)
    assert "source" in models and "plan" in models
    # Sparse kind ('plan') should fall back to global threshold
    global_m = calibration.fit(X)
    assert abs(models["plan"].threshold - global_m.threshold) < 1e-6


def test_calibration_model_serializes():
    m = calibration.fit(_make_benign(100))
    s = m.to_json()
    m2 = calibration.CalibrationModel.from_json(s)
    assert m2.n == m.n
    assert m2.threshold == m.threshold
    assert m2.threshold_ci95 == m.threshold_ci95
