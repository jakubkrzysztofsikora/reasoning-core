"""Smoke tests for eval/synthetic_drift.py — CUSUM + drift threshold derivation."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

np = pytest.importorskip("numpy")

spec = importlib.util.spec_from_file_location(
    "synthetic_drift", REPO_ROOT / "eval" / "synthetic_drift.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def test_benign_walk_shape():
    rng = np.random.default_rng(0)
    seq = mod._benign_walk(rng, T=20, dim=9)
    assert seq.shape == (20, 9)


def test_pivot_walk_returns_pivot_index():
    rng = np.random.default_rng(0)
    seq, t_star = mod._pivot_walk(rng, T=40, dim=9)
    assert seq.shape == (40, 9)
    assert 10 <= t_star < 30  # T/4..3T/4 = 10..30


def test_cusum_monotone_on_increasing_input():
    scores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    cusum = mod._cusum(scores, ref=0.0, drift=0.0)
    assert cusum.shape == scores.shape
    assert np.all(np.diff(cusum) >= -1e-9)


def test_cusum_resets_baseline():
    # Drift large enough to keep S negative early; running max should hold
    scores = np.array([0.0, 0.0, 0.0, 0.0, 10.0])
    cusum = mod._cusum(scores, ref=0.0, drift=0.5)
    assert cusum[-1] > cusum[0]
