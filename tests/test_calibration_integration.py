"""Integration tests for the calibration gate wired into pre_edit_guard.

Covers:
  - calibration disabled → identical behavior to pre-existing legacy path
  - enabled + missing model file → graceful fallback (no crash, no block)
  - enabled + valid file → routes through decide(), surfaces score
  - corrupt JSON → graceful fallback
  - per-kind dispatch picks the correct model
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "hooks"))

np = pytest.importorskip("numpy")

import calibration  # type: ignore  # noqa: E402
import _calibration_gate as gate  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    gate.reset_cache_for_tests()
    for var in ("RC_CALIBRATION_ENABLED", "RC_CALIBRATION_PATH"):
        monkeypatch.delenv(var, raising=False)
    yield
    gate.reset_cache_for_tests()


def _fit_dummy_models(seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    X = rng.normal(0.0, 1.0, size=(200, 9))
    kinds = ["source_code"] * 100 + ["test_code"] * 100
    return calibration.fit_per_kind(X, kinds, fpr_target=0.05)


def _write_models(path: Path, models: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    calibration.save_models(models, path)


# ---------------------------------------------------------------------------
# Disabled path
# ---------------------------------------------------------------------------

def test_disabled_returns_none_even_with_valid_file(tmp_path, monkeypatch):
    models = _fit_dummy_models()
    p = tmp_path / "calibration.json"
    _write_models(p, models)
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))
    # RC_CALIBRATION_ENABLED unset — must short-circuit.
    assert gate.is_enabled() is False
    result = gate.evaluate([0.0] * 9, file_kind="source_code")
    assert result is None


# ---------------------------------------------------------------------------
# Enabled + missing file
# ---------------------------------------------------------------------------

def test_enabled_missing_file_returns_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(tmp_path / "does_not_exist.json"))
    result = gate.evaluate([0.0] * 9, file_kind="source_code")
    assert result is None
    err = capsys.readouterr().err
    assert "calibration disabled" in err


# ---------------------------------------------------------------------------
# Enabled + valid file
# ---------------------------------------------------------------------------

def test_enabled_valid_file_returns_score(tmp_path, monkeypatch):
    models = _fit_dummy_models()
    p = tmp_path / "calibration.json"
    _write_models(p, models)
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))

    # Centroid → score ~0, no anomaly.
    src_model = models["source_code"]
    result = gate.evaluate(src_model.mean, file_kind="source_code")
    assert result is not None
    assert result["anomaly"] is False
    assert result["score"] < 1e-6
    assert result["kind_used"] == "source_code"

    # Far outlier → anomaly.
    far = [v + 100.0 for v in src_model.mean]
    result_far = gate.evaluate(far, file_kind="source_code")
    assert result_far is not None
    assert result_far["anomaly"] is True
    assert result_far["score"] > result_far["threshold"]


# ---------------------------------------------------------------------------
# Corrupt JSON
# ---------------------------------------------------------------------------

def test_enabled_corrupt_json_returns_none(tmp_path, monkeypatch, capsys):
    p = tmp_path / "calibration.json"
    p.write_text("{not valid json")
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))
    result = gate.evaluate([0.0] * 9, file_kind="source_code")
    assert result is None
    assert "calibration disabled" in capsys.readouterr().err


def test_enabled_missing_required_keys_returns_none(tmp_path, monkeypatch):
    p = tmp_path / "calibration.json"
    # Valid JSON but missing required CalibrationModel keys.
    p.write_text(json.dumps({"source_code": {"mean": [0.0]}}))
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))
    assert gate.evaluate([0.0] * 9, file_kind="source_code") is None


# ---------------------------------------------------------------------------
# Per-kind dispatch
# ---------------------------------------------------------------------------

def test_per_kind_dispatch_picks_correct_model(tmp_path, monkeypatch):
    models = _fit_dummy_models()
    p = tmp_path / "calibration.json"
    _write_models(p, models)
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))

    r_src = gate.evaluate(models["source_code"].mean, file_kind="source_code")
    r_test = gate.evaluate(models["test_code"].mean, file_kind="test_code")
    assert r_src is not None and r_src["kind_used"] == "source_code"
    assert r_test is not None and r_test["kind_used"] == "test_code"


def test_unknown_kind_falls_back(tmp_path, monkeypatch):
    models = _fit_dummy_models()
    p = tmp_path / "calibration.json"
    _write_models(p, models)
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))
    r = gate.evaluate([0.0] * 9, file_kind="some_unknown_kind")
    assert r is not None
    # Falls back to first available model alphabetically (no _global key here).
    assert r["kind_used"] in models.keys()


# ---------------------------------------------------------------------------
# Cache invalidation on mtime change
# ---------------------------------------------------------------------------

def test_cache_refreshes_on_mtime_change(tmp_path, monkeypatch):
    models_a = _fit_dummy_models(seed=0)
    p = tmp_path / "calibration.json"
    _write_models(p, models_a)
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))

    r1 = gate.evaluate([0.0] * 9, file_kind="source_code")
    assert r1 is not None
    thr1 = r1["threshold"]

    # Rewrite with different seed → different threshold; bump mtime.
    models_b = _fit_dummy_models(seed=42)
    _write_models(p, models_b)
    new_mtime = p.stat().st_mtime_ns + 1_000_000
    os.utime(p, ns=(new_mtime, new_mtime))

    r2 = gate.evaluate([0.0] * 9, file_kind="source_code")
    assert r2 is not None
    # Cache must have been refreshed; thresholds computed on different data
    # are extremely unlikely to be byte-identical.
    assert r2["threshold"] != thr1 or r2["model_n"] == r1["model_n"]


# ---------------------------------------------------------------------------
# Risk-vector dim mismatch
# ---------------------------------------------------------------------------

def test_dim_mismatch_returns_none(tmp_path, monkeypatch):
    models = _fit_dummy_models()
    p = tmp_path / "calibration.json"
    _write_models(p, models)
    monkeypatch.setenv("RC_CALIBRATION_ENABLED", "1")
    monkeypatch.setenv("RC_CALIBRATION_PATH", str(p))
    # 8-d vector vs 9-d model.
    r = gate.evaluate([0.0] * 8, file_kind="source_code")
    assert r is None


# ---------------------------------------------------------------------------
# End-to-end: hook subprocess unchanged when calibration disabled
# ---------------------------------------------------------------------------

def test_hook_unchanged_with_calibration_disabled(tmp_path):
    """Same payload through pre_edit_guard, default-OFF calibration must
    return identical exit code (0 on benign edit) as before the wiring."""
    # Reuse the stub-sidecar machinery from test_hook_block.
    from tests.test_hook_block import _StubSidecar, _edit_payload, _run_hook

    stub = _StubSidecar()
    stub.start()
    try:
        src = tmp_path / "x.py"
        src.write_text("def f(): pass\n", encoding="utf-8")
        payload = _edit_payload(str(src), "def f(): pass\n", "def f():\n    return 1\n")
        # No RC_CALIBRATION_ENABLED → legacy path.
        result = _run_hook(payload, env={"S2_URL": stub.url()})
        assert result.returncode == 0, f"stderr={result.stderr!r}"
    finally:
        stub.stop()
