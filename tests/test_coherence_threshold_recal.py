"""U2: coherence threshold recalibration 0.5 → 0.09 (empirical p95).

Audit 2026-06-01 §1.3 showed 98% of edits register coherence_delta < 0.10
with the old 0.5 threshold. New default is the empirical 95th percentile.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _clear_threshold_env(monkeypatch):
    monkeypatch.delenv("S2_COHERENCE_THRESHOLD", raising=False)
    monkeypatch.delenv("S2_AIS_THRESHOLD", raising=False)
    monkeypatch.delenv("S2_RISK_DIM_THRESHOLD", raising=False)


def _reload_s2_core():
    """Reload src.s2_core so module-level _env_float calls re-read env."""
    if "src.s2_core" in sys.modules:
        return importlib.reload(sys.modules["src.s2_core"])
    return importlib.import_module("src.s2_core")


def test_default_threshold_is_empirical_p95():
    s2 = _reload_s2_core()
    assert s2.COHERENCE_DELTA_THRESHOLD == pytest.approx(0.09)


def test_env_override_takes_effect(monkeypatch):
    monkeypatch.setenv("S2_COHERENCE_THRESHOLD", "0.25")
    s2 = _reload_s2_core()
    assert s2.COHERENCE_DELTA_THRESHOLD == pytest.approx(0.25)


def test_per_kind_thresholds_match_recal():
    s2 = _reload_s2_core()
    assert s2._KIND_THRESHOLDS["source_code"]["cd"] == pytest.approx(0.09)
    assert s2._KIND_THRESHOLDS["test_code"]["cd"] == pytest.approx(0.14)
    assert s2._KIND_THRESHOLDS["plan_md"]["cd"] == pytest.approx(0.30)
    assert s2._KIND_THRESHOLDS["doc_md"]["cd"] == pytest.approx(0.30)
    assert s2._KIND_THRESHOLDS["config"]["cd"] == pytest.approx(0.08)


def test_chord_distance_bounded_in_0_2():
    """Sanity: _l2_distance on L2-normalized vectors yields [0, 2]."""
    torch = pytest.importorskip("torch")
    s2 = _reload_s2_core()
    a = torch.tensor([1.0, 0.0, 0.0])
    b = torch.tensor([-1.0, 0.0, 0.0])
    d = s2._l2_distance(a, b)
    # antipodal L2-normed vectors → chord distance 2.0
    assert 0.0 <= d <= 2.0 + 1e-6
    assert d == pytest.approx(2.0, abs=1e-5)
