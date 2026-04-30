"""RC-010: tests for the FastMCP `reason_over_edit` bridge.

The bridge calls the local S2 sidecar over HTTP via httpx. We monkeypatch
``httpx.Client`` so no live sidecar is required.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

httpx = pytest.importorskip("httpx")
pytest.importorskip("mcp.server.fastmcp")


@pytest.fixture
def mcp_module(monkeypatch):
    """Reload src.mcp_reasoner cleanly per-test so env changes apply."""
    # Make sure both env knobs start from a known state.
    monkeypatch.delenv("S2_FAIL_CLOSED", raising=False)
    monkeypatch.delenv("S2_TIMEOUT", raising=False)
    monkeypatch.delenv("S2_URL", raising=False)
    if "src.mcp_reasoner" in sys.modules:
        del sys.modules["src.mcp_reasoner"]
    import src.mcp_reasoner as mod  # type: ignore
    return mod


def _impact_report() -> Dict[str, Any]:
    return {
        "architectural_impact_score": 0.92,
        "coherence_delta": 0.18,
        "risk_vector": [0.1, 0.2, 0.05, 0.1, 0.05, 0.1, 0.0, 0.1],
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth",
            "churn", "coupling", "cohesion", "novelty",
        ],
        "regression_detected": False,
        "human_summary": "looks fine",
    }


# ---------------------------------------------------------------------------
# Stubbing helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, payload: Optional[Dict[str, Any]] = None,
                 raw: Optional[bytes] = None):
        self.status_code = status_code
        self._payload = payload
        self._raw = raw

    def json(self):
        if self._raw is not None:
            raise ValueError("invalid json")
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.Client used as a context manager."""

    def __init__(self, *, response: Optional[_FakeResponse] = None,
                 raise_exc: Optional[BaseException] = None,
                 captured: Optional[list] = None,
                 **kwargs):
        self._response = response
        self._raise = raise_exc
        self._captured = captured if captured is not None else []
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, **kwargs):
        self._captured.append({"url": url, "json": json, "kwargs": kwargs})
        if self._raise is not None:
            raise self._raise
        return self._response


def _install_client(monkeypatch, mcp_module, *, response=None, raise_exc=None):
    captured: list = []

    def _factory(*args, **kwargs):
        return _FakeClient(response=response, raise_exc=raise_exc, captured=captured, **kwargs)

    monkeypatch.setattr(mcp_module.httpx, "Client", _factory)
    return captured


def _underlying(tool_obj) -> Any:
    """FastMCP wraps the registered callable; reach the underlying function."""
    for attr in ("fn", "func", "_fn", "_func", "callable", "_callable"):
        f = getattr(tool_obj, attr, None)
        if callable(f):
            return f
    if callable(tool_obj):
        return tool_obj
    raise AttributeError(f"could not unwrap MCP tool object {tool_obj!r}")


@pytest.fixture
def reason_callable(mcp_module):
    """Return a plain Python callable for reason_over_edit, bypassing MCP plumbing."""
    fn = getattr(mcp_module, "reason_over_edit", None)
    if fn is None:
        pytest.skip("reason_over_edit not exported as a top-level callable")
    if callable(fn):
        # FastMCP may decorate with FunctionTool; unwrap if needed.
        try:
            fn("/dev/null", "x")  # smoke call to detect direct callability
        except TypeError:
            fn = _underlying(fn)
        except Exception:
            # The smoke call may raise for legitimate reasons (network); that's
            # fine -- it just means fn is directly callable.
            pass
    else:
        fn = _underlying(fn)
    return fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_server_name_is_hybrid_reasoner(mcp_module):
    name = getattr(mcp_module.mcp, "name", None)
    if name is None:
        # Older FastMCP versions expose name via _name.
        name = getattr(mcp_module.mcp, "_name", None)
    assert name == "hybrid-reasoner"


def test_happy_path_returns_impact_report(mcp_module, reason_callable, monkeypatch):
    expected = _impact_report()
    captured = _install_client(
        monkeypatch, mcp_module,
        response=_FakeResponse(200, payload=expected),
    )

    result = reason_callable("/tmp/foo.py", "def f(): pass", "edit")
    assert isinstance(result, dict)
    for key in (
        "architectural_impact_score",
        "coherence_delta",
        "risk_vector",
        "regression_detected",
        "human_summary",
    ):
        assert key in result
    assert len(result["risk_vector"]) == 8
    assert result["regression_detected"] is False
    # Sanity: bridge actually called the sidecar /score endpoint.
    assert captured, "bridge did not call httpx.Client.post"
    assert captured[0]["url"].endswith("/score")
    sent = captured[0]["json"]
    assert sent["path"] == "/tmp/foo.py"
    assert sent["after_src"] == "def f(): pass"


def test_fail_open_when_sidecar_unreachable(mcp_module, reason_callable, monkeypatch):
    _install_client(
        monkeypatch, mcp_module,
        raise_exc=httpx.ConnectError("connection refused"),
    )
    result = reason_callable("/tmp/foo.py", "def f(): pass", "edit")
    assert result["regression_detected"] is False
    assert result["degraded"] is True


def test_fail_closed_env_flips_regression(mcp_module, reason_callable, monkeypatch):
    monkeypatch.setenv("S2_FAIL_CLOSED", "1")
    # Reload the module to pick up the new env var if the implementation reads
    # it at import time.
    if "src.mcp_reasoner" in sys.modules:
        del sys.modules["src.mcp_reasoner"]
    import src.mcp_reasoner as fresh  # type: ignore

    captured: list = []
    def _factory(*args, **kwargs):
        return _FakeClient(raise_exc=httpx.TimeoutException("timeout"),
                           captured=captured, **kwargs)
    monkeypatch.setattr(fresh.httpx, "Client", _factory)

    fn = getattr(fresh, "reason_over_edit", None)
    fn = _underlying(fn) if not callable(fn) or hasattr(fn, "fn") else fn
    try:
        result = fn("/tmp/foo.py", "def f(): pass", "edit")
    except TypeError:
        # Fall back to underlying.
        fn = _underlying(getattr(fresh, "reason_over_edit"))
        result = fn("/tmp/foo.py", "def f(): pass", "edit")

    assert result["regression_detected"] is True
    assert result["degraded"] is True


def test_unsupported_language_passthrough(mcp_module, reason_callable, monkeypatch):
    body = {"error": "unsupported_language", "extension": ".rb"}
    _install_client(
        monkeypatch, mcp_module,
        response=_FakeResponse(415, payload=body),
    )
    result = reason_callable("/tmp/foo.rb", "def x; end", "edit")
    assert result["regression_detected"] is False
    assert result["degraded"] is True
    assert result["reason"] == "unsupported_language"
    assert result["extension"] == ".rb"


def test_write_against_missing_file_does_not_raise(mcp_module, reason_callable, monkeypatch, tmp_path):
    expected = _impact_report()
    captured = _install_client(
        monkeypatch, mcp_module,
        response=_FakeResponse(200, payload=expected),
    )
    nonexistent = tmp_path / "subdir" / "newfile.py"
    # No exception even though the file does not exist.
    result = reason_callable(str(nonexistent), "def created(): pass", "write")
    assert isinstance(result, dict)
    assert "regression_detected" in result
    # Bridge should have sent before_src as empty string for a missing file.
    assert captured
    assert captured[0]["json"]["before_src"] == ""


def test_unexpected_5xx_treated_as_degraded(mcp_module, reason_callable, monkeypatch):
    _install_client(
        monkeypatch, mcp_module,
        response=_FakeResponse(503, payload={"error": "internal"}),
    )
    result = reason_callable("/tmp/foo.py", "def f(): pass", "edit")
    assert result["degraded"] is True
    assert result["regression_detected"] is False  # default fail-open


def test_invalid_sidecar_json_treated_as_degraded(mcp_module, reason_callable, monkeypatch):
    _install_client(
        monkeypatch, mcp_module,
        response=_FakeResponse(200, raw=b"not json"),
    )
    result = reason_callable("/tmp/foo.py", "def f(): pass", "edit")
    assert result["degraded"] is True


def test_module_runnable_as_main(mcp_module):
    """`python3 -m src.mcp_reasoner` should be runnable; we just check the
    module exposes an `mcp` runnable and a `__main__` guard."""
    assert hasattr(mcp_module, "mcp")
    # Must have a `run` method (FastMCP convention).
    assert callable(getattr(mcp_module.mcp, "run", None))
