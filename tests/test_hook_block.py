"""RC-011: tests for src/hooks/pre_edit_guard.py and .claude/settings.json.

The hook script reads a synthetic Claude Code PreToolUse JSON payload from
stdin and exits 0 (allow), 2 (block) per its contract. We invoke the script
through subprocess with a stub HTTP sidecar baked into the test fixture.

Stubbing the sidecar: we boot a tiny in-process http.server on a free port
that returns a configurable canned response. The hook is started with
S2_URL pointing at that stub.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import closing
from typing import Any, Dict, Iterator, Optional, Tuple

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "src", "hooks", "pre_edit_guard.py")
SETTINGS_PATH = os.path.join(REPO_ROOT, ".claude", "settings.json")


# ---------------------------------------------------------------------------
# Stub sidecar
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubSidecar:
    """An in-process /score stub. The response is configurable per test."""

    def __init__(self):
        self.port = _free_port()
        self._response: Tuple[int, Dict[str, Any]] = (200, {
            "architectural_impact_score": 1.0,
            "coherence_delta": 0.0,
            "risk_vector": [0.0] * 8,
            "risk_labels": [
                "cyclomatic", "fan_in", "fan_out", "depth",
                "churn", "coupling", "cohesion", "novelty",
            ],
            "regression_detected": False,
            "human_summary": "ok",
        })
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def set_response(self, status: int, body: Dict[str, Any]) -> None:
        self._response = (status, body)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):  # silence noise
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                _ = self.rfile.read(length)
                status, body = outer._response
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        self._server = socketserver.TCPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def stub_sidecar() -> Iterator[_StubSidecar]:
    s = _StubSidecar()
    s.start()
    try:
        yield s
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_hook(payload: Dict[str, Any], *, env: Optional[Dict[str, str]] = None,
              timeout: float = 15.0) -> subprocess.CompletedProcess:
    real_env = os.environ.copy()
    if env:
        real_env.update(env)
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=real_env,
        timeout=timeout,
    )


def _edit_payload(file_path: str, before: str, after: str) -> Dict[str, Any]:
    return {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": file_path,
            "old_string": before,
            "new_string": after,
        },
    }


# ---------------------------------------------------------------------------
# Settings.json validation
# ---------------------------------------------------------------------------

def test_settings_json_parses():
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)


def test_settings_pretooluse_matcher_includes_edit_write_multiedit():
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    hooks = data.get("hooks") or {}
    pretool = hooks.get("PreToolUse") or []
    assert isinstance(pretool, list) and pretool, "PreToolUse hooks missing"
    # At least one entry must have a matcher covering the three tool names.
    found = False
    for entry in pretool:
        matcher = entry.get("matcher", "")
        if all(t in matcher for t in ("Edit", "Write", "MultiEdit")):
            found = True
            break
    assert found, f"no PreToolUse entry covers Edit|Write|MultiEdit: {pretool}"


def test_settings_registers_hybrid_reasoner_mcp_server():
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    servers = data.get("mcpServers") or {}
    assert "hybrid-reasoner" in servers, f"mcpServers missing hybrid-reasoner: {servers}"


# ---------------------------------------------------------------------------
# Hook subprocess tests
# ---------------------------------------------------------------------------

def test_benign_edit_exits_zero(stub_sidecar, tmp_path):
    # Default stub response is regression_detected=False.
    src_file = tmp_path / "x.py"
    src_file.write_text("def f(): pass\n", encoding="utf-8")
    payload = _edit_payload(str(src_file), "def f(): pass\n", "def f():\n    return 1\n")
    result = _run_hook(payload, env={"S2_URL": stub_sidecar.url()})
    assert result.returncode == 0, f"stderr={result.stderr!r}"


def test_regressing_edit_exits_two(stub_sidecar, tmp_path):
    stub_sidecar.set_response(200, {
        "architectural_impact_score": 0.1,
        "coherence_delta": 2.5,
        "risk_vector": [0.95, 0.5, 0.5, 0.4, 0.3, 0.2, 0.1, 0.7],
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth",
            "churn", "coupling", "cohesion", "novelty",
        ],
        "regression_detected": True,
        "human_summary": "guard removed; unbounded recursion",
    })
    src_file = tmp_path / "regressing.py"
    src_file.write_text(
        "def f(n):\n    if not n:\n        return 0\n    return n + f(n - 1)\n",
        encoding="utf-8",
    )
    payload = _edit_payload(
        str(src_file),
        "def f(n):\n    if not n:\n        return 0\n    return n + f(n - 1)\n",
        "def f(n):\n    return n + f(n + 1)\n",
    )
    result = _run_hook(payload, env={"S2_URL": stub_sidecar.url()})
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}; stderr={result.stderr!r}"
    assert result.stderr.strip(), "expected non-empty stderr reason"


def test_unsupported_language_exits_zero_with_note(stub_sidecar, tmp_path):
    stub_sidecar.set_response(415, {
        "error": "unsupported_language",
        "extension": ".rb",
    })
    rb_file = tmp_path / "sample.rb"
    rb_file.write_text("def greet; end\n", encoding="utf-8")
    payload = _edit_payload(str(rb_file), "def greet; end\n", "def greet; \"hi\"; end\n")
    result = _run_hook(payload, env={"S2_URL": stub_sidecar.url()})
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "unsupported_language" in result.stderr


def test_sidecar_offline_default_fail_open(tmp_path):
    """Without S2_FAIL_CLOSED, an unreachable sidecar must NOT block the user."""
    src_file = tmp_path / "x.py"
    src_file.write_text("x = 1\n", encoding="utf-8")
    payload = _edit_payload(str(src_file), "x = 1\n", "x = 2\n")
    # Point at a dead port; use a short timeout.
    dead = f"http://127.0.0.1:{_free_port()}"
    result = _run_hook(
        payload,
        env={"S2_URL": dead, "S2_TIMEOUT": "2"},
        timeout=15.0,
    )
    assert result.returncode == 0, (
        f"expected fail-open exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )


def test_sidecar_offline_fail_closed_blocks(tmp_path):
    """With S2_FAIL_CLOSED=1 the same scenario must exit 2."""
    src_file = tmp_path / "x.py"
    src_file.write_text("x = 1\n", encoding="utf-8")
    payload = _edit_payload(str(src_file), "x = 1\n", "x = 2\n")
    dead = f"http://127.0.0.1:{_free_port()}"
    result = _run_hook(
        payload,
        env={"S2_URL": dead, "S2_TIMEOUT": "2", "S2_FAIL_CLOSED": "1"},
        timeout=15.0,
    )
    assert result.returncode == 2, (
        f"expected fail-closed exit 2, got {result.returncode}; stderr={result.stderr!r}"
    )


def test_malformed_stdin_does_not_block():
    """Garbage stdin must not break the developer flow -- hook exits 0."""
    real_env = os.environ.copy()
    real_env["S2_URL"] = f"http://127.0.0.1:{_free_port()}"
    real_env["S2_TIMEOUT"] = "2"
    result = subprocess.run(
        [sys.executable, HOOK_PATH],
        input="not json{",
        capture_output=True,
        text=True,
        env=real_env,
        timeout=10.0,
    )
    assert result.returncode == 0


def test_multiedit_first_regression_blocks(stub_sidecar, tmp_path):
    """For a MultiEdit payload, the hook must fail on the first regression."""
    stub_sidecar.set_response(200, {
        "architectural_impact_score": 0.1,
        "coherence_delta": 3.0,
        "risk_vector": [0.95] + [0.0] * 7,
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth",
            "churn", "coupling", "cohesion", "novelty",
        ],
        "regression_detected": True,
        "human_summary": "bad",
    })
    src_file = tmp_path / "x.py"
    src_file.write_text("a = 1\n", encoding="utf-8")
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(src_file),
            "edits": [
                {"old_string": "a = 1\n", "new_string": "a = 2\n"},
                {"old_string": "a = 2\n", "new_string": "a = 3\n"},
            ],
        },
    }
    result = _run_hook(payload, env={"S2_URL": stub_sidecar.url()})
    assert result.returncode == 2, f"stderr={result.stderr!r}"
