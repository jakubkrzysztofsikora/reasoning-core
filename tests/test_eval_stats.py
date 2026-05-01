"""Tests for eval.stats (RC-208 statistical helpers)."""

from __future__ import annotations

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest

from eval.stats import bca_bootstrap, holm_bonferroni, paired_wilcoxon


def test_paired_wilcoxon_all_positive_low_p():
    deltas = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    p = paired_wilcoxon(deltas)
    assert 0.0 <= p <= 1.0
    assert p < 0.05


def test_paired_wilcoxon_balanced_high_p():
    deltas = [-0.1, 0.1, -0.2, 0.2, -0.3, 0.3]
    p = paired_wilcoxon(deltas)
    assert 0.0 <= p <= 1.0
    assert p > 0.5


def test_paired_wilcoxon_all_zero_returns_nan():
    p = paired_wilcoxon([0.0, 0.0, 0.0])
    assert math.isnan(p)


def test_bca_bootstrap_constant_deltas_collapses_to_value():
    lo, hi = bca_bootstrap([0.5] * 10, n=200, seed=1)
    assert lo == hi == 0.5


def test_bca_bootstrap_ci_brackets_mean():
    deltas = [-1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]
    mean = sum(deltas) / len(deltas)
    lo, hi = bca_bootstrap(deltas, n=400, seed=42)
    assert lo <= mean <= hi


def test_bca_bootstrap_empty_returns_nan():
    lo, hi = bca_bootstrap([], n=100)
    assert math.isnan(lo) and math.isnan(hi)


def test_bca_bootstrap_singleton():
    lo, hi = bca_bootstrap([3.14], n=100)
    assert lo == hi == 3.14


def test_holm_bonferroni_orders_and_caps():
    pvals = [0.01, 0.02, 0.03, 0.04]
    adj = holm_bonferroni(pvals)
    assert len(adj) == 4
    # Holm-adjusted is monotone non-decreasing in original p order here.
    for a in adj:
        assert 0.0 <= a <= 1.0
    assert adj[0] <= adj[1] <= adj[2] <= adj[3]


def test_holm_bonferroni_handles_nan():
    adj = holm_bonferroni([float("nan"), 0.01, 0.5])
    assert math.isnan(adj[0])
    assert 0.0 <= adj[1] <= 1.0
    assert 0.0 <= adj[2] <= 1.0
