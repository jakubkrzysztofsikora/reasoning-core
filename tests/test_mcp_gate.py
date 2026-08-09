"""Tests for src/mcp_gate.gate_edit (Phase 4)."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src import mcp_gate  # noqa: E402


class _MockClient:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self._sc = status_code
        self._p = payload or {}
        self._raise = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None):
        if self._raise:
            raise self._raise
        r = type("R", (), {"status_code": self._sc, "json": lambda self_=None: self._p})()
        r.json = lambda: self._p
        return r


def test_allow_when_no_regression(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("RC_HOST", "vibe")
    monkeypatch.setattr(
        mcp_gate, "httpx",
        type("M", (), {"Client": lambda *a, **kw: _MockClient(payload={"regression_detected": False, "human_summary": "ok"}),
                       "HTTPError": Exception, "TimeoutException": Exception})(),
    )
    out = mcp_gate.gate_edit("/x.py", "before", "after")
    assert out["decision"] == "allowed"
    assert out["regression_detected"] is False


def test_block_when_regression(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("RC_HOST", "vibe")
    # Legacy direct neural blocking remains available only as an explicit
    # compatibility override; the release default is corroborated scoring.
    monkeypatch.setenv("RC_NEURAL_CORROBORATED", "0")
    monkeypatch.setattr(
        mcp_gate, "httpx",
        type("M", (), {"Client": lambda *a, **kw: _MockClient(payload={"regression_detected": True, "human_summary": "REGRESSION"}),
                       "HTTPError": Exception, "TimeoutException": Exception})(),
    )
    out = mcp_gate.gate_edit("/x.py", "before", "after")
    assert out["decision"] == "blocked"
    assert out["regression_detected"] is True
    assert out["message"] == "REGRESSION"


def test_unsupported_language_allows(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        mcp_gate, "httpx",
        type("M", (), {"Client": lambda *a, **kw: _MockClient(status_code=415, payload={"error": "unsupported_language"}),
                       "HTTPError": Exception, "TimeoutException": Exception})(),
    )
    out = mcp_gate.gate_edit("/x.rb", "before", "after")
    assert out["decision"] == "allowed"
    assert out.get("unsupported_language") is True


def test_audit_row_written_with_fsync(monkeypatch, tmp_path):
    from src.hooks import audit_log as _al
    monkeypatch.setattr(_al, "_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("RC_HOST", "vibe")
    monkeypatch.setattr(
        mcp_gate, "httpx",
        type("M", (), {"Client": lambda *a, **kw: _MockClient(payload={"regression_detected": False, "human_summary": "ok"}),
                       "HTTPError": Exception, "TimeoutException": Exception})(),
    )
    mcp_gate.gate_edit("/x.py", "before", "after")
    jsonls = list(tmp_path.rglob("*.jsonl"))
    assert jsonls, "no audit row produced"
    rows = jsonls[0].read_text().strip().splitlines()
    assert any("gate_edit" in r and "vibe" in r for r in rows)
