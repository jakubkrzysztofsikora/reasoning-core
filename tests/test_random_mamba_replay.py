"""Tests for the offline random-mamba replay harness."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from eval.random_mamba_replay import (
    BUILTIN_FIXTURES,
    _agreement,
    _build_corpus,
    _cohen_kappa,
    _compute_precision,
    _event_replayability,
    _extract_pair_from_event,
    _iter_audit_events,
    _load_fixtures,
    run_replay,
)


def test_event_replayability_detects_missing_fields():
    """Events without source/token/diff fields are not replayable."""
    event = {"file_path": "x.py", "decision": "allowed", "before_bytes": 10}
    rep = _event_replayability(event)
    assert rep["is_replayable"] is False
    assert rep["present_fields"] == []


def test_event_replayability_detects_source_pair():
    event = {
        "file_path": "x.py",
        "before_src": "def f(): pass",
        "after_src": "def f(): return 1",
    }
    rep = _event_replayability(event)
    assert rep["is_replayable"] is True
    assert rep["has_source_pair"] is True


def test_extract_pair_from_event_prefers_source():
    event = {
        "file_path": "src/x.py",
        "before_src": "before code",
        "after_src": "after code",
        "diff_hunk": "diff",
        "decision_id": "abc123",
        "decision": "blocked",
        "session_id": "sess-1",
    }
    pair = _extract_pair_from_event(event)
    assert pair is not None
    assert pair["before"] == "before code"
    assert pair["after"] == "after code"
    assert pair["source"] == "audit_event"


def test_extract_pair_from_event_token_stream_fallback():
    event = {
        "file_path": "src/y.py",
        "ast_token_stream": "<function> y",
        "decision_id": "def456",
    }
    pair = _extract_pair_from_event(event)
    assert pair is not None
    assert pair["after"] == "<function> y"
    assert pair["source"] == "audit_token_stream"


def test_build_corpus_includes_builtins_and_replayable_events():
    events = [
        {"file_path": "a.py", "before_src": "x", "after_src": "y"},
        {"file_path": "b.py", "decision": "allowed"},  # not replayable
    ]
    corpus = _build_corpus(events, [], limit=None)
    sources = {p["source"] for p in corpus}
    assert "audit_event" in sources
    assert "builtin_fixture" in sources
    assert len(corpus) == len(BUILTIN_FIXTURES) + 1


def test_build_corpus_respects_limit():
    events = [{"file_path": f"{i}.py", "before_src": "x", "after_src": "y"} for i in range(100)]
    corpus = _build_corpus(events, [], limit=5)
    assert len(corpus) == 5


def test_load_fixtures_skips_invalid_json():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixtures.jsonl"
        path.write_text(
            '{"path":"a.py","before":"x","after":"y"}\n'
            'not json\n'
            '{"path":"b.py","before":"x"}\n'  # missing after
        )
        fixtures = _load_fixtures(path)
    assert len(fixtures) == 1
    assert fixtures[0]["path"] == "a.py"


def test_agreement_and_kappa():
    a = [True, True, False, False]
    b = [True, False, True, False]
    agg = _agreement(a, b)
    assert agg["n"] == 4
    assert agg["both_flagged"] == 1
    assert agg["only_real_flagged"] == 1
    assert agg["only_random_flagged"] == 1
    assert agg["neither_flagged"] == 1
    assert agg["agreement_rate"] == 0.5
    assert -1.0 <= agg["cohen_kappa"] <= 1.0


def test_kappa_perfect_agreement():
    a = [True, True, False]
    b = [True, True, False]
    assert _cohen_kappa(a, b) == 1.0


def test_compute_precision():
    labels = ["regression", "ok", "regression", "ok", "ok"]
    flags = [True, False, True, True, False]
    p = _compute_precision(labels, flags)
    assert p["true_positives"] == 2
    assert p["false_positives"] == 1
    assert p["false_negatives"] == 0
    assert p["true_negatives"] == 2
    assert p["precision"] == pytest.approx(0.6667)
    assert p["recall"] == 1.0


def test_run_replay_with_mocked_scoring(tmp_path, monkeypatch):
    """End-to-end smoke of the harness with deterministic mocked scores."""
    audit_root = tmp_path / "events"
    day_dir = audit_root / "2026-07-09"
    day_dir.mkdir(parents=True)
    (day_dir / "sess.jsonl").write_text(
        json.dumps({
            "file_path": "src/real.py",
            "before_src": "def a(): pass",
            "after_src": "def a(): return 1",
            "decision_id": "d1",
            "decision": "allowed",
        }) + "\n" +
        json.dumps({"file_path": "src/missing.py", "decision": "allowed"}) + "\n"
    )

    fake_scores = {
        "mamba-130m": [
            {"regression_detected": True, "ais": 0.8, "coherence_delta": 0.15, "risk_vector": [0.1] * 8, "fired_dims": ["novelty"]},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
        ],
        "random-mamba": [
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
        ],
    }
    score_iter = {k: iter(v) for k, v in fake_scores.items()}

    def _fake_score_pairs(pairs, embedder):
        it = score_iter[embedder]
        out = []
        for _ in pairs:
            try:
                out.append(next(it))
            except StopIteration:
                it = iter(fake_scores[embedder])
                out.append(next(it))
        return out

    monkeypatch.setattr("eval.random_mamba_replay._score_pairs", _fake_score_pairs)

    output = tmp_path / "report.json"
    scaffold = tmp_path / "scaffold.csv"
    report = run_replay(
        audit_root=audit_root,
        fixtures_path=None,
        output_path=output,
        scaffold_path=scaffold,
        labels_path=None,
        embedders=["mamba-130m", "random-mamba"],
        limit=None,
    )

    assert report["n_audit_events"] == 2
    assert report["replayability"]["replayable_events"] == 1
    assert report["corpus"]["n_pairs"] == 1 + len(BUILTIN_FIXTURES)
    assert report["embedders"]["mamba-130m"]["regression_flags"] == 1
    assert report["embedders"]["random-mamba"]["regression_flags"] == 0
    assert report["verdict"]["is_ssm_signal_non_null"] is True

    assert output.exists()
    with output.open() as fh:
        saved = json.load(fh)
    assert saved["corpus"]["n_pairs"] == report["corpus"]["n_pairs"]

    assert scaffold.exists()
    with scaffold.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2  # one row per embedder for the single flagged pair


def test_run_replay_with_labels(tmp_path, monkeypatch):
    """Precision is computed when a labeled scaffold is supplied."""
    audit_root = tmp_path / "events"
    day_dir = audit_root / "2026-07-09"
    day_dir.mkdir(parents=True)
    (day_dir / "sess.jsonl").write_text(
        json.dumps({
            "file_path": "src/real.py",
            "before_src": "def a(): pass",
            "after_src": "def a(): return 1",
            "decision_id": "d1",
            "decision": "allowed",
        }) + "\n"
    )

    fake_scores = {
        "mamba-130m": [
            {"regression_detected": True, "ais": 0.8, "coherence_delta": 0.15, "risk_vector": [0.1] * 8, "fired_dims": ["novelty"]},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
        ],
        "random-mamba": [
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
            {"regression_detected": False, "ais": 0.95, "coherence_delta": 0.05, "risk_vector": [0.0] * 8, "fired_dims": []},
        ],
    }
    score_iter = {k: iter(v) for k, v in fake_scores.items()}

    def _fake_score_pairs(pairs, embedder):
        it = score_iter[embedder]
        out = []
        for _ in pairs:
            try:
                out.append(next(it))
            except StopIteration:
                it = iter(fake_scores[embedder])
                out.append(next(it))
        return out

    monkeypatch.setattr("eval.random_mamba_replay._score_pairs", _fake_score_pairs)

    labels = tmp_path / "labels.csv"
    # Label only the audit-event row for mamba-130m as a true regression.
    labels.write_text(
        "row_id,pair_id,embedder,file_path,source,original_decision,"
        "regression_detected,coherence_delta,ais,fired_dims,ground_truth_label,notes\n"
        "r1,d1,mamba-130m,src/real.py,audit_event,allowed,True,0.15,0.8,novelty,regression,\n"
        "r2,d1,random-mamba,src/real.py,audit_event,allowed,False,0.05,0.95,,ok,\n"
    )

    report = run_replay(
        audit_root=audit_root,
        fixtures_path=None,
        output_path=None,
        scaffold_path=None,
        labels_path=labels,
        embedders=["mamba-130m", "random-mamba"],
        limit=None,
    )

    assert report["precision"]["mamba-130m"]["true_positives"] == 1
    assert report["precision"]["mamba-130m"]["false_positives"] == 0
    assert report["precision"]["mamba-130m"]["precision"] == 1.0
    assert report["precision"]["random-mamba"]["true_positives"] == 0
    assert report["precision"]["random-mamba"]["false_positives"] == 0


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("RC_SKIP_SLOW") == "1",
    reason="RC_SKIP_SLOW=1",
)
def test_random_mamba_loads_offline():
    """Verify the random-mamba embedder can load without network credentials."""
    import tempfile
    import shutil

    empty_hf = tempfile.mkdtemp()
    try:
        os.environ["HF_HOME"] = empty_hf
        os.environ["RC_EMBEDDER"] = "random-mamba"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"

        from src import ssm_backbone
        from src.ssm_backbone import _OfflineTokenizer, embed, load_backbone, reset_failure_cache

        reset_failure_cache()
        ssm_backbone._HANDLE = None
        model, tok = load_backbone()
        assert model is not None
        # The tokenizer should be the offline one when gpt2 is not cached.
        assert isinstance(tok, _OfflineTokenizer)
        v = embed("def foo(): return 1")
        assert len(v.shape) == 1
        assert v.shape[0] == 768
    finally:
        shutil.rmtree(empty_hf, ignore_errors=True)
        for key in ("HF_HOME", "RC_EMBEDDER", "TRANSFORMERS_OFFLINE", "HF_HUB_OFFLINE"):
            os.environ.pop(key, None)
