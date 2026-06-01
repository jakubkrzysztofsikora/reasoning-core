"""U3: session_id fallback to audit_log._session_id().

Audit 2026-06-01 §Summary #1: CLAUDE_SESSION_ID is rarely set in real
hook invocations, so the Phase-2 risk dims (session_centroid_drift,
project_fan_in, project_coupling) never fire. Fall back to the stable
per-process id audit_log already uses.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from contextlib import closing
from typing import Any, Dict, Iterator, List, Optional

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "src", "hooks", "pre_edit_guard.py")


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CapturingStub:
    """Stub /score sidecar that captures POSTed JSON payloads."""

    def __init__(self):
        self.port = _free_port()
        self.payloads: List[Dict[str, Any]] = []
        self._server: Optional[socketserver.TCPServer] = None
        self._body = json.dumps({
            "architectural_impact_score": 1.0,
            "coherence_delta": 0.0,
            "risk_vector": [0.0] * 8,
            "risk_labels": [
                "cyclomatic", "fan_in", "fan_out", "depth",
                "churn", "coupling", "cohesion", "novelty",
            ],
            "regression_detected": False,
            "human_summary": "ok",
        }).encode("utf-8")

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **kw):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length)
                try:
                    outer.payloads.append(json.loads(raw))
                except Exception:
                    outer.payloads.append({"_invalid": raw[:80].decode(errors="replace")})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer._body)))
                self.end_headers()
                self.wfile.write(outer._body)

            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        self._server = socketserver.TCPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def capturing_stub() -> Iterator[_CapturingStub]:
    s = _CapturingStub()
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _run_hook(payload: Dict[str, Any], env: Dict[str, str]) -> subprocess.CompletedProcess:
    real_env = os.environ.copy()
    for var in (
        "S2_FAIL_CLOSED", "RC_ALLOW_GUARD_EDIT", "S2_URL", "S2_TIMEOUT",
        "S2_HARD_CAP_MS", "RC_LANG_LOCK", "RC_STATE_DIR", "RC_TASK_SPEC",
        "RC_BEST_EFFORT_SPEC", "RC_PLAN_GROUNDING", "RC_RUN_DIR",
        "CLAUDE_SESSION_ID",
    ):
        real_env.pop(var, None)
    real_env.update(env)
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=real_env,
        timeout=15,
    )


def _edit(file_path: str, before: str, after: str) -> Dict[str, Any]:
    return {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": file_path,
            "old_string": before,
            "new_string": after,
        },
    }


def test_session_id_from_claude_env_when_set(capturing_stub, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    proc = _run_hook(
        _edit(str(src), "return 1", "return 2"),
        env={
            "S2_URL": capturing_stub.url(),
            "CLAUDE_SESSION_ID": "test-claude-sid-1234",
            "S2_HARD_CAP_MS": "5000",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert capturing_stub.payloads, "stub never received a POST"
    assert capturing_stub.payloads[0].get("session_id") == "test-claude-sid-1234"


def test_session_id_falls_back_to_audit_log(capturing_stub, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    proc = _run_hook(
        _edit(str(src), "return 1", "return 2"),
        env={
            "S2_URL": capturing_stub.url(),
            "S2_HARD_CAP_MS": "5000",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert capturing_stub.payloads, "stub never received a POST"
    sid = capturing_stub.payloads[0].get("session_id")
    assert sid is not None, f"no session_id in payload: {capturing_stub.payloads[0]}"
    # audit_log._session_id returns "anon-<12hex>" by default
    assert isinstance(sid, str) and len(sid) > 0
