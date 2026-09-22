"""BLOCKER #1 regression tests: mahal_anomaly in the PRODUCTION path.

The 2026-09-22 hostile review verified that the previous Phase C
landing shipped a ``mahal_anomaly`` field that no production code
path could populate:

* Nothing writes ``_BASELINES[session_id]["__corpus__"]`` (the
  ``/baseline`` route writes per-file ``__corpus__`` at the path
  level, not the session level).
* Nothing writes ``_BASELINES[session_id]["__mahal_threshold__"]``.
* The 22 unit + 7 wiring tests seeded fixture state that the
  production path can never reach.

This test exercises the FULL production path with no monkeypatching
of the mahal_signal internals:

    score_change(path, before_src, after_src, session_id)
        -> session accumulates >= N path baselines
        -> session-level __corpus__ is built from accumulated paths
        -> Ledoit-Wolf shrinks it into __mahal_mean__ / __mahal_inv__
        -> FPR=0.05 threshold is persisted as __mahal_threshold__
        -> ImpactReport.mahal_anomaly is populated on the next call
        -> "mahal_anomaly_above_threshold" fires when the OOD after-
           embedding exceeds the persisted threshold

The pre-fix baseline guarantees this is a BLOCKER: before the fix,
``score_change`` accumulates N baselines but never folds them into
a session-level corpus, so the field stays None forever.
"""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import torch

from src import s2_core


# Sizing: we need at least this many path baselines in a session
# before the session-level corpus is built. The rollup plan
# pre-registered an N >= 5 floor; the implementation may pick a
# different default that we honour.
MIN_BASELINES_FOR_CORPUS = int(os.environ.get("RC_MAHAL_CORPUS_MIN", "5"))


@pytest.fixture
def tiny_embedder(monkeypatch):
    """Install a real (small) embedder returning torch tensors.

    The embedder returns a torch tensor whose values are a
    deterministic function of the source text. Two different texts
    produce vectors in clearly separated regions so coherence_delta /
    AIS / novelty are reproducible.
    """
    hidden = 16  # small dim so test is fast and shapes match

    def _embed_tokens(text, **kwargs):
        if not text:
            return torch.zeros(hidden, dtype=torch.float32)
        seed = abs(hash(str(text))) % (2**32)
        rng = np.random.default_rng(seed)
        return torch.from_numpy(rng.normal(scale=0.3, size=hidden).astype(np.float32))

    monkeypatch.setattr(s2_core, "embed", _embed_tokens)
    monkeypatch.setattr(s2_core, "BACKBONE_INFO", {"hidden_size": hidden})
    return _embed_tokens


def _drive_session(monkeypatch, session_id, n_paths, hidden=16):
    """Drive N path edits through score_change to populate _BASELINES."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for i in range(n_paths):
            p = Path(td) / f"file_{i}.py"
            # Same template, distinct files => distinct embeddings.
            s2_core.score_change(
                path=str(p),
                before_src=f"x_{i} = {i}\n",
                after_src=f"x_{i} = {i + 100}\n",
                session_id=session_id,
            )


# ---------------------------------------------------------------------------
# BLOCKER #1 test: production path populates mahal_anomaly after
# accumulating enough session baselines.
# ---------------------------------------------------------------------------


def test_production_path_populates_mahal_anomaly_after_session_accumulates(
    monkeypatch, tiny_embedder
):
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_production_wire"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    # After accumulating enough baselines, the session-level corpus
    # must exist (BLOCKER #1: pre-fix this dict was empty).
    baselines = s2_core._BASELINES.get(sid, {})
    corpus_blob = baselines.get("__corpus__") if isinstance(baselines, dict) else None
    assert corpus_blob is not None, (
        f"BLOCKER #1 STILL PRESENT: _BASELINES[{sid!r}].__corpus__ is None "
        f"after {MIN_BASELINES_FOR_CORPUS} path edits; production never "
        f"folds per-path baselines into a session-level corpus. "
        f"Available keys: {sorted(baselines.keys()) if isinstance(baselines, dict) else 'not a dict'}"
    )


def test_production_path_persists_mahal_threshold(
    monkeypatch, tiny_embedder
):
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_threshold"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    baselines = s2_core._BASELINES.get(sid, {})
    thr = baselines.get("__mahal_threshold__") if isinstance(baselines, dict) else None
    assert thr is not None, (
        f"BLOCKER #1 STILL PRESENT: _BASELINES[{sid!r}].__mahal_threshold__ "
        f"is None; no production path computes the FPR=0.05 threshold."
    )
    assert isinstance(thr, float)
    assert thr > 0.0


def test_production_path_emits_mahal_anomaly_in_impact_report(
    monkeypatch, tiny_embedder
):
    """Drive N path baselines, then a fresh edit must surface mahal_anomaly."""
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_emit"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    with tempfile.TemporaryDirectory() as td:
        report = s2_core.score_change(
            path=str(Path(td) / "fresh.py"),
            before_src="fresh_before = 0\n",
            after_src="fresh_after = 1\n",
            session_id=sid,
        )
    # The field must be populated end-to-end via the production path.
    assert report.mahal_anomaly is not None, (
        f"BLOCKER #1 STILL PRESENT: ImpactReport.mahal_anomaly is None "
        f"after {MIN_BASELINES_FOR_CORPUS} path baselines; the production "
        f"path never reads the corpus even when it exists."
    )
    assert isinstance(report.mahal_anomaly, float)
    assert math.isfinite(report.mahal_anomaly)


def test_production_path_lazy_fit_when_only_corpus_present(
    monkeypatch, tiny_embedder
):
    """If a pre-existing session has only __corpus__ (no fitted
    mean/inv), the production path must lazy-fit them on read.
    """
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_lazy"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    # Drop the fitted tensors to simulate a pre-Phase-C baseline
    # manifest that has only the corpus, no fitted model.
    baselines = s2_core._BASELINES.get(sid, {})
    if isinstance(baselines, dict):
        baselines.pop("__mahal_mean__", None)
        baselines.pop("__mahal_inv__", None)

    with tempfile.TemporaryDirectory() as td:
        report = s2_core.score_change(
            path=str(Path(td) / "lazy.py"),
            before_src="a = 1\n",
            after_src="a = 2\n",
            session_id=sid,
        )
    assert report.mahal_anomaly is not None, (
        "lazy-fit-on-read did not fire; production path requires the "
        "fitted tensors to be pre-computed and refuses to fit them on "
        "demand."
    )
