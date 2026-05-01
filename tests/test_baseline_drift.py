"""Tests for the /metrics and /baseline endpoints + cumulative_drift in score_change.

Heavy deps (torch, transformers, tree_sitter) are gated with importorskip.
The SSM backbone is loaded once via the session-scoped ``loaded_backbone``
fixture pattern (mirrors test_s2_core.py) to keep the test suite cheap.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(scope="module")
def s2_core_module():
    pytest.importorskip("tree_sitter")
    try:
        import src.s2_core as mod  # type: ignore
    except Exception as exc:
        pytest.skip(f"src.s2_core import failed: {exc}")
    return mod


@pytest.fixture(scope="module")
def loaded_backbone():
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    import src.ssm_backbone as ssm  # type: ignore

    try:
        ssm.load_backbone()
    except ssm.BackboneUnavailableError as exc:
        pytest.skip(f"backbone unavailable: {exc}")
    except Exception as exc:
        pytest.skip(f"backbone load raised: {exc}")
    return ssm


@pytest.fixture(scope="module")
def http_client(s2_core_module, loaded_backbone):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    app = s2_core_module.create_app()
    with TestClient(app) as client:
        yield client


# --- ImpactReport schema ---------------------------------------------------

def test_impact_report_cumulative_drift_default_none(s2_core_module):
    rep = s2_core_module.ImpactReport(
        architectural_impact_score=0.5,
        coherence_delta=0.1,
        risk_vector=[0.0] * 8,
        regression_detected=False,
        human_summary="ok",
    )
    d = rep.to_dict()
    # Default None -> field omitted, preserving backward compat with consumers.
    assert "cumulative_drift" not in d


def test_impact_report_cumulative_drift_serializes_when_set(s2_core_module):
    rep = s2_core_module.ImpactReport(
        architectural_impact_score=0.5,
        coherence_delta=0.1,
        risk_vector=[0.0] * 8,
        regression_detected=False,
        human_summary="ok",
        cumulative_drift=2.71,
    )
    d = rep.to_dict()
    assert d["cumulative_drift"] == pytest.approx(2.71)


# --- /metrics endpoint -----------------------------------------------------

def test_metrics_endpoint_shape(http_client):
    resp = http_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("score_calls", "p50_ms", "p95_ms", "p99_ms", "errors", "uptime_s"):
        assert key in body


def test_metrics_records_score_call(http_client, s2_core_module):
    # Reset the ring buffer through the private helpers (test-only access).
    s2_core_module._METRICS_LATENCIES.clear()
    s2_core_module._METRICS_ERRORS = 0
    PY = "def f():\n    return 1\n"
    resp = http_client.post(
        "/score",
        json={"path": "/tmp/m.py", "before_src": PY, "after_src": PY},
    )
    assert resp.status_code == 200
    metrics = http_client.get("/metrics").json()
    assert metrics["score_calls"] >= 1
    assert metrics["p50_ms"] >= 0


def test_percentile_helper_deterministic(s2_core_module):
    # _percentile is a pure function operating on sorted float lists.
    vs = sorted([float(x) for x in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)])
    assert s2_core_module._percentile(vs, 50) == pytest.approx(55.0)
    assert s2_core_module._percentile(vs, 95) == pytest.approx(95.5)
    assert s2_core_module._percentile(vs, 99) == pytest.approx(99.1)
    assert s2_core_module._percentile([], 50) == 0.0


# --- /baseline endpoint ----------------------------------------------------

def test_baseline_post_happy_path(http_client, s2_core_module):
    s2_core_module._clear_session_baselines()
    body = {
        "session_id": "sess-A",
        "files": [
            {"path": "a.py", "src": "def a():\n    return 1\n"},
            {"path": "b.py", "src": "def b():\n    return 2\n"},
        ],
    }
    resp = http_client.post("/baseline", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["session_id"] == "sess-A"
    assert payload["n_files"] == 2
    assert payload["hidden_size"] > 0


def test_baseline_post_rejects_bad_body(http_client):
    # Missing session_id.
    r = http_client.post("/baseline", json={"files": [{"path": "x.py", "src": "x = 1"}]})
    assert r.status_code == 400
    # Missing files.
    r = http_client.post("/baseline", json={"session_id": "x"})
    assert r.status_code == 400


def test_score_with_session_id_returns_cumulative_drift(http_client, s2_core_module):
    s2_core_module._clear_session_baselines()
    # Build a baseline of a few similar files.
    base_src = "def f():\n    return 1\n"
    http_client.post("/baseline", json={
        "session_id": "sess-B",
        "files": [
            {"path": "a.py", "src": base_src},
            {"path": "b.py", "src": base_src},
        ],
    })
    # Now score against the same session.
    resp = http_client.post("/score", json={
        "path": "/tmp/c.py",
        "before_src": base_src,
        "after_src": base_src,
        "session_id": "sess-B",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "cumulative_drift" in body
    assert isinstance(body["cumulative_drift"], (int, float))


def test_score_without_session_id_omits_cumulative_drift(http_client, s2_core_module):
    PY = "def f():\n    return 1\n"
    resp = http_client.post("/score", json={
        "path": "/tmp/d.py",
        "before_src": PY,
        "after_src": PY,
    })
    assert resp.status_code == 200
    body = resp.json()
    # Field is omitted when no baseline is set.
    assert "cumulative_drift" not in body


def test_score_with_unknown_session_omits_cumulative_drift(http_client, s2_core_module):
    s2_core_module._clear_session_baselines()
    PY = "def f():\n    return 1\n"
    resp = http_client.post("/score", json={
        "path": "/tmp/e.py",
        "before_src": PY,
        "after_src": PY,
        "session_id": "no-baseline-session",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "cumulative_drift" not in body
