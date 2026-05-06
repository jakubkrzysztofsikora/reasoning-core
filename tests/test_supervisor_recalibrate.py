"""Tests for src/_supervisor_recalibrate.py — signal → refit pipeline."""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "_supervisor_recalibrate",
    REPO_ROOT / "src" / "_supervisor_recalibrate.py",
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "eval" / "runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _good_payload() -> str:
    return json.dumps({
        "triggered": True,
        "reasons": ["page_hinkley_alarm:2026-05-01"],
        "loc_churn": 0.0,
        "target_fpr": 0.02,
    })


def _stub_mine(rows):
    def _mine(repo, since, out):
        with open(out, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return {"negative_source_code": len(rows)}
    return _mine


def _stub_score(_path, _before, _after):
    return SimpleNamespace(risk_vector=[0.1] * 8)


def test_missing_signal_is_noop(tmp_path):
    repo = _make_repo(tmp_path)
    called = {"fit": 0, "save": 0}

    def fit(*_a, **_kw):
        called["fit"] += 1
        return {}

    def save(*_a, **_kw):
        called["save"] += 1

    mod._tick(repo, mine_fn=_stub_mine([]), score_fn=_stub_score,
              fit_fn=fit, save_fn=save)
    assert called == {"fit": 0, "save": 0}


def test_corrupt_signal_does_not_crash_and_stays(tmp_path):
    repo = _make_repo(tmp_path)
    sig = mod._signal_path(repo)
    sig.write_text("not json {{{")

    def fit(*_a, **_kw):
        raise AssertionError("must not be called on corrupt signal")

    def save(*_a, **_kw):
        raise AssertionError("must not be called on corrupt signal")

    # Should not raise.
    mod._tick(repo, mine_fn=_stub_mine([]), score_fn=_stub_score,
              fit_fn=fit, save_fn=save)
    assert sig.exists(), "corrupt signal must remain for operator inspection"


def test_successful_refit_unlinks_signal(tmp_path):
    repo = _make_repo(tmp_path)
    sig = mod._signal_path(repo)
    sig.write_text(_good_payload())

    rows = [
        {"path": "src/a.py", "label": "negative", "file_kind": "source_code",
         "before": "x=1", "after": "x=2"},
        {"path": "src/b.py", "label": "negative", "file_kind": "source_code",
         "before": "y=1", "after": "y=2"},
        {"path": "tests/c.py", "label": "positive", "file_kind": "test_code",
         "before": "z=1", "after": "z=2"},  # filtered out (positive)
    ]
    saved = {}

    def fit(X, kinds):
        # Only the 2 negative rows reach the fitter.
        assert X.shape == (2, 8)
        assert kinds == ["source_code", "source_code"]
        return {"source_code": "model"}

    def save(models, path):
        saved["models"] = models
        saved["path"] = path

    mod._tick(repo, mine_fn=_stub_mine(rows), score_fn=_stub_score,
              fit_fn=fit, save_fn=save)
    assert not sig.exists(), "signal must be cleared after success"
    assert saved["models"] == {"source_code": "model"}
    assert saved["path"] == mod._calibration_path(repo)


def test_failed_refit_leaves_signal_in_place(tmp_path):
    repo = _make_repo(tmp_path)
    sig = mod._signal_path(repo)
    sig.write_text(_good_payload())

    rows = [{"path": "src/a.py", "label": "negative",
             "file_kind": "source_code", "before": "x=1", "after": "x=2"}]

    def fit(_X, _kinds):
        raise RuntimeError("synthetic refit failure")

    def save(*_a, **_kw):
        raise AssertionError("save must not run when fit fails")

    mod._tick(repo, mine_fn=_stub_mine(rows), score_fn=_stub_score,
              fit_fn=fit, save_fn=save)
    assert sig.exists(), "signal must persist after refit failure"


def test_no_benign_rows_aborts_refit(tmp_path):
    repo = _make_repo(tmp_path)
    sig = mod._signal_path(repo)
    sig.write_text(_good_payload())

    def fit(*_a, **_kw):
        raise AssertionError("must not call fit with empty X")

    def save(*_a, **_kw):
        raise AssertionError("must not call save with empty X")

    mod._tick(repo, mine_fn=_stub_mine([]), score_fn=_stub_score,
              fit_fn=fit, save_fn=save)
    assert sig.exists()


def test_watcher_honors_stop_event(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("RC_RECALIBRATE_POLL_S", "1")
    stop = threading.Event()
    ticks = {"n": 0}

    def fake_tick(*_a, **_kw):
        ticks["n"] += 1

    monkeypatch.setattr(mod, "_tick", fake_tick)

    t = threading.Thread(
        target=mod.recalibrate_watcher,
        args=(stop, repo),
        kwargs={"poll_s": 0.05},
        daemon=True,
    )
    t.start()
    # Let it tick a couple of times.
    stop_after = threading.Timer(0.2, stop.set)
    stop_after.start()
    t.join(timeout=2.0)
    stop_after.cancel()
    assert not t.is_alive(), "watcher must exit after stop.set()"
    assert ticks["n"] >= 1


def test_poll_seconds_env_clamped(monkeypatch):
    monkeypatch.setenv("RC_RECALIBRATE_POLL_S", "bogus")
    assert mod._poll_seconds() == 60.0
    monkeypatch.setenv("RC_RECALIBRATE_POLL_S", "0.01")
    # min clamp is 1.0
    assert mod._poll_seconds() == 1.0
    monkeypatch.setenv("RC_RECALIBRATE_POLL_S", "30")
    assert mod._poll_seconds() == 30.0


def test_signal_present_detected(tmp_path):
    repo = _make_repo(tmp_path)
    sig = mod._signal_path(repo)
    sig.write_text(_good_payload())
    payload = mod._read_signal(sig)
    assert payload is not None
    assert "page_hinkley_alarm:2026-05-01" in payload["reasons"]
