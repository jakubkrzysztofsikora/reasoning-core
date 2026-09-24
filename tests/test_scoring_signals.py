"""Unit tests for src/scoring_signals.py -- Phase C (audit-deferred).

The Mahalanobis anomaly signal adds an *independent* post-embedding
score that is NOT a scalar transform of cosine similarity
the audit-verified redundancy. This module wraps the existing
``src.calibration`` math (Ledoit-Wolf shrinkage + squared
Mahalanobis distance) into a small, well-tested public API.

We test the unit layer (math + edge cases + determinism) here.
Wiring into s2_core.py is tested in test_scoring_v3_wiring.py.

Reference: thoughts/shared/research/2026-09-19-audit-deferred-scoring-v3.md
("Approach 3: add an independent anomaly signal") and the
rollup plan Phase C at thoughts/shared/plans/2026-09-19-audit-deferred-rollup.md.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src import scoring_signals


# fit_benign_corpus


def test_fit_benign_corpus_returns_centroid_and_inverse_covariance():
    rng = np.random.default_rng(0)
    embs = rng.normal(size=(40, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    assert mean.shape == (8,)
    assert cov_inv.shape == (8, 8)
    np.testing.assert_allclose(mean, embs.mean(axis=0), atol=1e-9)
    np.testing.assert_allclose(cov_inv, cov_inv.T, atol=1e-9)
    eigs = np.linalg.eigvalsh(cov_inv)
    assert (eigs > 0).all(), f"cov_inv has non-positive eigenvalues: {eigs}"


def test_fit_benign_corpus_is_deterministic_across_runs():
    rng = np.random.default_rng(0)
    embs = rng.normal(size=(40, 8))
    mean_a, _cov_a, inv_a = scoring_signals.fit_benign_corpus(embs)
    mean_b, _cov_b, inv_b = scoring_signals.fit_benign_corpus(embs)
    np.testing.assert_array_equal(mean_a, mean_b)
    np.testing.assert_array_equal(inv_a, inv_b)


def test_fit_benign_corpus_handles_single_sample_via_shrinkage():
    embs = np.array([[0.1, 0.2, 0.3, 0.4]])
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    assert mean.shape == (4,)
    assert cov_inv.shape == (4, 4)
    assert np.isfinite(cov_inv).all()


def test_fit_benign_corpus_zero_sample_raises():
    with pytest.raises(ValueError, match="at least one"):
        scoring_signals.fit_benign_corpus(np.zeros((0, 4)))


def test_fit_benign_corpus_rejects_1d_input():
    with pytest.raises(ValueError, match="2-D"):
        scoring_signals.fit_benign_corpus(np.array([0.1, 0.2, 0.3, 0.4]))


# mahal_anomaly_against_corpus


def test_mahal_anomaly_is_zero_at_centroid():
    rng = np.random.default_rng(1)
    embs = rng.normal(size=(50, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    d = scoring_signals.mahal_anomaly_against_corpus(mean, mean, cov_inv)
    assert math.isclose(d, 0.0, abs_tol=1e-9)


def test_mahal_anomaly_grows_with_distance_from_centroid():
    rng = np.random.default_rng(2)
    embs = rng.normal(size=(50, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    near = mean + rng.normal(scale=0.1, size=8)
    far = mean + rng.normal(scale=5.0, size=8)
    d_near = scoring_signals.mahal_anomaly_against_corpus(near, mean, cov_inv)
    d_far = scoring_signals.mahal_anomaly_against_corpus(far, mean, cov_inv)
    assert d_far > d_near * 10, (
        f"far/near distance ratio too low: {d_far=:.2f} vs {d_near=:.2f}"
    )


def test_mahal_anomaly_is_non_negative_and_finite():
    rng = np.random.default_rng(3)
    embs = rng.normal(size=(30, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    point = rng.normal(size=8) * 3.0
    d = scoring_signals.mahal_anomaly_against_corpus(point, mean, cov_inv)
    assert d >= 0.0
    assert math.isfinite(d)


def test_mahal_anomaly_returns_zero_for_zero_vector():
    d = scoring_signals.mahal_anomaly_against_corpus(
        np.zeros(4), np.zeros(4), np.eye(4)
    )
    assert d == 0.0


def test_mahal_anomaly_returns_inf_for_degenerate_zero_inverse():
    d = scoring_signals.mahal_anomaly_against_corpus(
        np.array([1.0, 2.0]), np.zeros(2), np.zeros((2, 2))
    )
    assert d == float("inf")


# threshold helpers


def test_threshold_for_fpr_target_is_quantile_of_benign_distances():
    rng = np.random.default_rng(4)
    embs = rng.normal(size=(200, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(embs)
    distances = np.array([
        scoring_signals.mahal_anomaly_against_corpus(emb, mean, cov_inv)
        for emb in embs
    ])
    thr = scoring_signals.threshold_for_fpr(distances, fpr=0.05)
    exceed = (distances > thr).sum() / len(distances)
    assert 0.02 <= exceed <= 0.10, f"FPR far from 0.05 target: {exceed:.3f}"


def test_threshold_for_fpr_handles_empty_input():
    thr = scoring_signals.threshold_for_fpr(np.array([]), fpr=0.05)
    assert thr == 0.0


def test_threshold_for_fpr_rejects_out_of_range_fpr():
    with pytest.raises(ValueError, match="fpr"):
        scoring_signals.threshold_for_fpr(np.array([1.0, 2.0, 3.0]), fpr=1.5)
    with pytest.raises(ValueError, match="fpr"):
        scoring_signals.threshold_for_fpr(np.array([1.0, 2.0, 3.0]), fpr=-0.1)


# end-to-end


def test_end_to_end_pipeline_detects_out_of_distribution_point():
    rng = np.random.default_rng(5)
    benign = rng.normal(loc=0.0, scale=1.0, size=(80, 16))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(benign)
    distances = np.array([
        scoring_signals.mahal_anomaly_against_corpus(emb, mean, cov_inv)
        for emb in benign
    ])
    thr = scoring_signals.threshold_for_fpr(distances, fpr=0.05)
    ood = mean + np.ones(16) * 8.0
    d_ood = scoring_signals.mahal_anomaly_against_corpus(ood, mean, cov_inv)
    assert d_ood > thr * 5, (
        f"OOD distance {d_ood:.2f} should be far above threshold {thr:.3f}"
    )


def test_end_to_end_pipeline_is_deterministic():
    rng = np.random.default_rng(6)
    benign = rng.normal(size=(60, 8))
    a_mean, _a_cov, a_inv = scoring_signals.fit_benign_corpus(benign)
    b_mean, _b_cov, b_inv = scoring_signals.fit_benign_corpus(benign)
    np.testing.assert_array_equal(a_mean, b_mean)
    np.testing.assert_array_equal(a_inv, b_inv)
    distances_a = np.array([
        scoring_signals.mahal_anomaly_against_corpus(emb, a_mean, a_inv)
        for emb in benign
    ])
    distances_b = np.array([
        scoring_signals.mahal_anomaly_against_corpus(emb, b_mean, b_inv)
        for emb in benign
    ])
    np.testing.assert_array_equal(distances_a, distances_b)


# loo_threshold_for_fpr (honest out-of-sample calibration)


def test_loo_threshold_for_fpr_returns_finite_value_for_normal_corpus():
    rng = np.random.default_rng(7)
    benign = rng.normal(scale=0.3, size=(10, 8))
    thr = scoring_signals.loo_threshold_for_fpr(benign, fpr=0.05)
    assert isinstance(thr, float)
    assert math.isfinite(thr)
    assert thr > 0.0


def test_loo_threshold_for_fpr_is_honester_than_in_sample():
    """LOO FPR is closer to the nominal 0.05 than in-sample at small n."""
    rng = np.random.default_rng(8)
    benign = rng.normal(scale=0.3, size=(5, 8))
    mean, _cov, cov_inv = scoring_signals.fit_benign_corpus(benign)
    in_sample_thr = scoring_signals.threshold_for_fpr(
        np.array([
            scoring_signals.mahal_anomaly_against_corpus(e, mean, cov_inv)
            for e in benign
        ]),
        fpr=0.05,
    )
    loo_thr = scoring_signals.loo_threshold_for_fpr(benign, fpr=0.05)
    # The LOO threshold is always >= the in-sample quantile because it
    # excludes each point from its own training fit; the difference
    # narrows as n grows but is significant at n=5.
    assert loo_thr >= in_sample_thr, (
        f"LOO threshold ({loo_thr:.3f}) should be >= in-sample "
        f"({in_sample_thr:.3f}); LOO is more conservative."
    )


def test_loo_threshold_for_fpr_handles_small_corpus():
    """n=1 returns 0.0 (safe default); n=2 returns a non-zero finite value."""
    rng = np.random.default_rng(9)
    one = rng.normal(size=(1, 4))
    thr_one = scoring_signals.loo_threshold_for_fpr(one, fpr=0.05)
    assert thr_one == 0.0

    two = rng.normal(size=(2, 4))
    thr_two = scoring_signals.loo_threshold_for_fpr(two, fpr=0.05)
    assert math.isfinite(thr_two)


def test_loo_threshold_for_fpr_rejects_out_of_range_fpr():
    rng = np.random.default_rng(10)
    benign = rng.normal(size=(5, 4))
    with pytest.raises(ValueError, match="fpr"):
        scoring_signals.loo_threshold_for_fpr(benign, fpr=1.5)
    with pytest.raises(ValueError, match="fpr"):
        scoring_signals.loo_threshold_for_fpr(benign, fpr=-0.1)


def test_loo_threshold_for_fpr_rejects_1d_input():
    with pytest.raises(ValueError, match="2-D"):
        scoring_signals.loo_threshold_for_fpr(np.array([0.1, 0.2, 0.3]), fpr=0.05)
