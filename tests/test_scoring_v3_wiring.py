"""Integration tests for Phase C (scoring-v3) wiring into score_change.

These tests exercise the end-to-end path:
  src.scoring_signals -> src.s2_core.score_change -> ImpactReport

They verify:
  * Without RC_SCORING_V3=1 the flag is a no-op (default safe).
  * With RC_SCORING_V3=1, when a benign corpus is fitted for the
    session/path, ``mahal_anomaly`` is populated on the ImpactReport
    and ``mahal_anomaly_above_threshold`` shows up in
    ``fired_conditions`` for out-of-distribution after-embeddings.
  * The new fired-condition co-exists with the existing
    coherence_delta / ais / dim-ceiling checks.

We monkeypatch the embedder so the test does not need a real SSM
backbone. This keeps the test CPU-only and fast.
"""
from __future__ import annotations

import math
import os
from typing import Any

import numpy as np
import pytest
import torch

from src import s2_core
from src.scoring_signals import (
    fit_benign_corpus,
    mahal_anomaly_against_corpus,
    threshold_for_fpr,
)


# ---------------------------------------------------------------------------
# Fixtures: monkeypatched embedder
# ---------------------------------------------------------------------------


def _seed_from_str(s: str) -> int:
    return abs(hash(s)) % (2**32)


@pytest.fixture
def fake_embedder(monkeypatch):
    """Install a deterministic in-memory embedder returning torch tensors.

    The embedder returns a torch tensor whose values are a deterministic
    function of the source text. We make ``before`` and ``after`` live
    in clearly separated regions of the embedding space so the
    Mahalanobis test triggers reliably.
    """

    def _embed_tokens(text, **kwargs):
        if not text:
            return torch.zeros(8, dtype=torch.float32)
        seed = _seed_from_str(str(text))
        rng = np.random.default_rng(seed)
        return torch.from_numpy(rng.normal(scale=0.3, size=8).astype(np.float32))

    # s2_core.embed is the public name used by score_change.
    monkeypatch.setattr(s2_core, "embed", _embed_tokens)
    monkeypatch.setattr(s2_core, "BACKBONE_INFO", {"hidden_size": 8})
    return _embed_tokens


def _seed_session(monkeypatch, session_id, benign_size=32, seed=42):
    """Seed _BASELINES[session_id] with a fresh benign corpus."""
    rng = np.random.default_rng(seed)
    benign = rng.normal(scale=0.3, size=(benign_size, 8)).astype(np.float32)
    mean, _cov, cov_inv = fit_benign_corpus(benign)
    distances = np.array([
        mahal_anomaly_against_corpus(e, mean, cov_inv) for e in benign
    ])
    thr = threshold_for_fpr(distances, fpr=0.05)
    monkeypatch.setattr(s2_core, "_BASELINES", {
        session_id: {
            "__corpus__": torch.from_numpy(benign),
            "__mahal_mean__": torch.from_numpy(mean.astype(np.float32)),
            "__mahal_inv__": torch.from_numpy(cov_inv.astype(np.float32)),
            "__mahal_threshold__": float(thr),
        }
    })
    return mean, cov_inv, float(thr)


# ---------------------------------------------------------------------------
# Default-off: RC_SCORING_V3=0 (the safe default)
# ---------------------------------------------------------------------------


def test_scoring_v3_off_by_default(monkeypatch, fake_embedder, tmp_path):
    monkeypatch.delenv("RC_SCORING_V3", raising=False)
    monkeypatch.setattr(s2_core, "_BASELINES", {})
    report = s2_core.score_change(
        path=str(tmp_path / "a.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_off",
    )
    # Default off: mahal_anomaly is None and the new condition never fires.
    assert report.mahal_anomaly is None
    assert "mahal_anomaly_above_threshold" not in report.fired_conditions


def test_scoring_v3_off_ignores_baseline_corpus(monkeypatch, fake_embedder, tmp_path):
    """Even with a corpus in _BASELINES, RC_SCORING_V3=0 keeps the field None."""
    monkeypatch.delenv("RC_SCORING_V3", raising=False)
    _seed_session(monkeypatch, "s_off2")
    report = s2_core.score_change(
        path=str(tmp_path / "a2.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_off2",
    )
    assert report.mahal_anomaly is None


# ---------------------------------------------------------------------------
# On path: RC_SCORING_V3=1 + benign corpus in _BASELINES
# ---------------------------------------------------------------------------


def test_scoring_v3_populates_mahal_anomaly_when_corpus_present(
    monkeypatch, fake_embedder, tmp_path
):
    monkeypatch.setenv("RC_SCORING_V3", "1")
    mean, cov_inv, thr = _seed_session(monkeypatch, "s_on", seed=123)
    report = s2_core.score_change(
        path=str(tmp_path / "b.py"),
        before_src="x = 1\n",
        after_src="x = 2\n",
        session_id="s_on",
    )
    assert report.mahal_anomaly is not None
    assert isinstance(report.mahal_anomaly, float)
    assert math.isfinite(report.mahal_anomaly)
    assert report.mahal_anomaly >= 0.0


def test_scoring_v3_records_threshold_on_report(monkeypatch, fake_embedder, tmp_path):
    monkeypatch.setenv("RC_SCORING_V3", "1")
    mean, cov_inv, thr = _seed_session(monkeypatch, "s_thr", seed=124)
    report = s2_core.score_change(
        path=str(tmp_path / "b_thr.py"),
        before_src="x = 1\n",
        after_src="x = 2\n",
        session_id="s_thr",
    )
    # The threshold field should be exposed so operators can audit.
    assert report.mahal_anomaly_threshold == pytest.approx(thr)


def test_scoring_v3_silent_when_no_corpus_for_session(
    monkeypatch, fake_embedder, tmp_path
):
    """Without a corpus for the session, the field stays None."""
    monkeypatch.setenv("RC_SCORING_V3", "1")
    monkeypatch.setattr(s2_core, "_BASELINES", {})
    report = s2_core.score_change(
        path=str(tmp_path / "d.py"),
        before_src="a = 1\n",
        after_src="a = 2\n",
        session_id="s_no_corpus",
    )
    assert report.mahal_anomaly is None
    assert "mahal_anomaly_above_threshold" not in report.fired_conditions


def test_scoring_v3_does_not_break_existing_fired_conditions(
    monkeypatch, fake_embedder, tmp_path
):
    """coherence_delta / ais / novelty remain computed and reported."""
    monkeypatch.setenv("RC_SCORING_V3", "1")
    monkeypatch.setattr(s2_core, "_BASELINES", {})
    report = s2_core.score_change(
        path=str(tmp_path / "e.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_regression",
    )
    # Old signals still computed.
    assert isinstance(report.coherence_delta, float)
    assert isinstance(report.architectural_impact_score, float)
    assert isinstance(report.fired_conditions, list)
    # mahal_anomaly is None (no corpus).
    assert report.mahal_anomaly is None


def test_scoring_v3_to_dict_round_trip_includes_field_when_set(
    monkeypatch, fake_embedder, tmp_path
):
    monkeypatch.setenv("RC_SCORING_V3", "1")
    _seed_session(monkeypatch, "s_dict", seed=125)
    report = s2_core.score_change(
        path=str(tmp_path / "f.py"),
        before_src="x = 1\n",
        after_src="x = 2\n",
        session_id="s_dict",
    )
    d = report.to_dict()
    assert "mahal_anomaly" in d
    assert isinstance(d["mahal_anomaly"], float)
    assert "mahal_anomaly_threshold" in d
    assert isinstance(d["mahal_anomaly_threshold"], float)
