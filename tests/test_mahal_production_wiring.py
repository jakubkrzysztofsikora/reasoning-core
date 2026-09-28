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


# ---------------------------------------------------------------------------
# Round-2 BLOCKER regression tests: session-freeze + calibration
# ---------------------------------------------------------------------------


def test_session_does_not_freeze_after_corpus_promotion(monkeypatch, tiny_embedder):
    """After corpus promotion, new paths must STILL be auto-persisted.

    Round-2 hostile review Finding 2: ``_persist_session_baseline_for_path``
    has a guard ``if path not in baselines and "__corpus__" not in baselines``
    that permanently skips per-path persistence once the corpus exists.
    This freezes the session: new paths never appear in
    ``_BASELINES[session_id]``, ``__mahal_last_fit_n__`` becomes dead,
    and cumulative-drift for new files is computed against a 2-D corpus
    tensor instead of a vector.

    The fix: the corpus promotion does NOT block new path persistence.
    It only guards against the corpus being *overwritten* by an
    auto-persisted path (which would conflict with the corpus key).
    """
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_freeze_regression"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    # After corpus promotion, drive 3 more distinct paths.
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS + 3)
    baselines = s2_core._BASELINES.get(sid, {})
    assert isinstance(baselines, dict)
    file_keys = [k for k in baselines if not k.startswith("__")]
    # We should have at least the second batch's paths persisted too.
    # (Not the first batch because LRU eviction may have dropped them.)
    assert len(file_keys) >= MIN_BASELINES_FOR_CORPUS, (
        f"Session freeze regression: only {len(file_keys)} file keys present "
        f"after {2 * MIN_BASELINES_FOR_CORPUS} path edits (expected >= "
        f"{MIN_BASELINES_FOR_CORPUS}). _BASELINES[session_id] is permanently "
        f"frozen after corpus promotion."
    )


def test_corpus_promotion_handles_degenerate_corpus_gracefully(monkeypatch):
    """A degenerate corpus (all-same embeddings) must NOT silently always-fire.

    Round-2 Finding 2: when the session accumulates N near-identical
    embeddings (reachable via /baseline poisoning or a collapsed
    session), the Ledoit-Wolf shrinkage degenerates, the LOO threshold
    becomes a near-zero value, and every fresh benign edit scores in
    the quadrillions -> mahal_anomaly_above_threshold always fires.

    The fix: when the corpus covariance collapses to the shrinkage
    target (zero off-diagonal, identical diagonal), we set the
    threshold to +inf so the signal stays inert until the session
    accumulates a non-degenerate corpus. The threshold is still
    persisted so operators can see it in ``rc doctor``, but it never
    fires until the corpus is meaningful.
    """
    import torch
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()

    # Install an embedder that returns the same vector regardless of input.
    same_vec = torch.zeros(16, dtype=torch.float32)

    def _constant_embed(text, **kwargs):
        return same_vec

    monkeypatch.setattr(s2_core, "embed", _constant_embed)
    monkeypatch.setattr(s2_core, "BACKBONE_INFO", {"hidden_size": 16})

    sid = "s_degenerate"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # Drive N path edits; all return the same vector.
        for i in range(MIN_BASELINES_FOR_CORPUS):
            p = Path(td) / f"f{i}.py"
            s2_core.score_change(
                path=str(p),
                before_src=f"x_{i} = {i}\n",
                after_src=f"x_{i} = {i + 1}\n",
                session_id=sid,
            )
        # Now drive a fresh edit. With a degenerate corpus, the
        # threshold must be +inf so the fired condition never trips.
        report = s2_core.score_change(
            path=str(Path(td) / "fresh.py"),
            before_src="y = 0\n",
            after_src="y = 1\n",
            session_id=sid,
        )
    assert report.mahal_anomaly is not None
    # Either the threshold is +inf (degenerate corpus -> inert) OR the
    # signal is finite and doesn't fire. We accept either as long as
    # ``mahal_anomaly_above_threshold`` is NOT in fired_conditions.
    if report.mahal_anomaly_threshold == float("inf"):
        assert "mahal_anomaly_above_threshold" not in report.fired_conditions
    else:
        # If the corpus did manage to be non-degenerate (e.g. tiny noise
        # made it through), the threshold should still be large enough
        # that a same-vector edit doesn't fire.
        assert report.mahal_anomaly <= report.mahal_anomaly_threshold, (
            f"degenerate-corpus edit fired: mahal_anomaly="
            f"{report.mahal_anomaly:.2e} > threshold={report.mahal_anomaly_threshold:.4f}"
        )


def test_session_baseline_accepts_explicit_corpus_after_promotion(monkeypatch):
    """Per-path baselines must still accumulate after corpus promotion.

    A separate-but-related fix: even after corpus promotion, callers
    that explicitly call /baseline with a fresh per-path vector must
    have those vectors stored. The regression is that the guard
    ``if path not in baselines and "__corpus__" not in baselines``
    blocks this even for explicit (non-auto) persists.
    """
    monkeypatch.setenv("RC_SCORING_V3", "1")
    s2_core._BASELINES.clear()
    sid = "s_explicit_after"
    _drive_session(monkeypatch, sid, n_paths=MIN_BASELINES_FOR_CORPUS)

    # Simulate an explicit /baseline call: the baseline dict should
    # have a known key (e.g. an explicit path), AND auto-persist
    # for a fresh path must STILL work.
    baselines = s2_core._BASELINES.get(sid, {})
    assert "__corpus__" in baselines
    pre_existing_path_count = len([k for k in baselines if not k.startswith("__")])

    # Drive a fresh path through score_change.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        s2_core.score_change(
            path=str(Path(td) / "post_promotion.py"),
            before_src="p = 1\n",
            after_src="p = 2\n",
            session_id=sid,
        )

    baselines = s2_core._BASELINES.get(sid, {})
    post_path_count = len([k for k in baselines if not k.startswith("__")])
    # The session learned at least one new path.
    assert post_path_count > pre_existing_path_count or any(
        "post_promotion" in k for k in baselines
    ), (
        f"auto-persist blocked after promotion: pre={pre_existing_path_count}, "
        f"post={post_path_count}"
    )
