"""U1: hard cap on sidecar /score POST.

Audit 2026-06-01 §1.4: ssm p99=60s; without a client-side cap the agent
stalls for a full minute per Edit. Default S2_HARD_CAP_MS=1500 forces
fall-back to the symbolic layer on slow sidecars.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
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


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SlowStubSidecar:
    """In-process /score stub with configurable response delay."""

    def __init__(self, delay_s: float = 0.0):
        self.port = _free_port()
        self.delay_s = delay_s
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None
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
                try:
                    _ = self.rfile.read(length)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                if outer.delay_s > 0:
                    time.sleep(outer.delay_s)
                # Client may have abandoned the connection during the sleep
                # (that is exactly the hard-cap path under test) — suppress
                # the BrokenPipe traceback so the server thread stays clean.
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(outer._body)))
                    self.end_headers()
                    self.wfile.write(outer._body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

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


@contextlib.contextmanager
def _stub(delay_s: float = 0.0) -> Iterator[_SlowStubSidecar]:
    s = _SlowStubSidecar(delay_s=delay_s)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _run_hook(payload: Dict[str, Any], *, env: Optional[Dict[str, str]] = None,
              timeout: float = 15.0) -> subprocess.CompletedProcess:
    real_env = os.environ.copy()
    for var in (
        "S2_FAIL_CLOSED", "S2_URL", "S2_TIMEOUT", "S2_HARD_CAP_MS",
        "RC_ALLOW_GUARD_EDIT", "RC_LANG_LOCK", "RC_STATE_DIR",
        "RC_BEST_EFFORT_SPEC", "RC_PLAN_GROUNDING", "RC_RUN_DIR",
    ):
        real_env.pop(var, None)
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


def test_fast_sidecar_completes_normally(tmp_path):
    """Stub responds within budget → hook allows."""
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    with _stub(delay_s=0.0) as stub:
        proc = _run_hook(
            _edit_payload(str(src), "return 1", "return 2"),
            env={"S2_URL": stub.url(), "S2_HARD_CAP_MS": "2000"},
        )
    assert proc.returncode == 0, proc.stderr
    assert "hard cap exceeded" not in proc.stderr


def test_slow_sidecar_triggers_hard_cap_fail_open(tmp_path):
    """Stub holds the response > S2_HARD_CAP_MS → fail-open + stderr breadcrumb."""
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    with _stub(delay_s=3.0) as stub:
        start = time.monotonic()
        proc = _run_hook(
            _edit_payload(str(src), "return 1", "return 2"),
            env={"S2_URL": stub.url(), "S2_HARD_CAP_MS": "500"},
            timeout=20.0,
        )
        elapsed = time.monotonic() - start
    # Fail-open (default S2_FAIL_CLOSED unset → 0): exit 0 on sidecar timeout.
    assert proc.returncode == 0, proc.stderr
    # Should NOT have waited the full 3s.
    assert elapsed < 2.5, f"hook waited {elapsed:.2f}s — hard cap didn't fire"
    assert "hard cap exceeded" in proc.stderr or "sidecar unavailable" in proc.stderr


def test_slow_sidecar_with_fail_closed_blocks(tmp_path):
    """S2_HARD_CAP_MS + S2_FAIL_CLOSED=1: hard cap now triggers symbolic
    fallback, so a clean edit no longer blocks. The fail-closed path is
    reserved for non-timeout sidecar unavailability."""
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    with _stub(delay_s=3.0) as stub:
        proc = _run_hook(
            _edit_payload(str(src), "return 1", "return 2"),
            env={
                "S2_URL": stub.url(),
                "S2_HARD_CAP_MS": "500",
                "S2_FAIL_CLOSED": "1",
            },
            timeout=20.0,
        )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "symbolic fallback engaged" in proc.stderr


def test_hard_cap_symbolic_fallback_emits_audit_event(tmp_path):
    """Hard cap + clean edit emits signal_source=symbolic_fallback audit row."""
    src = tmp_path / "x.py"
    src.write_text("def f(): return 1\n", encoding="utf-8")
    audit_root = tmp_path / "audit"
    with _stub(delay_s=3.0) as stub:
        proc = _run_hook(
            _edit_payload(str(src), "return 1", "return 2"),
            env={
                "S2_URL": stub.url(),
                "S2_HARD_CAP_MS": "500",
                "RC_AUDIT_ROOT": str(audit_root),
            },
            timeout=20.0,
        )
    assert proc.returncode == 0, proc.stderr
    # Find the JSONL file written today.
    day_dir = audit_root / _dt.date.today().isoformat()
    jsonl_files = list(day_dir.glob("*.jsonl"))
    assert jsonl_files, "expected an audit JSONL file"
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert lines
    event = json.loads(lines[-1])
    assert event.get("signal_source") == "symbolic_fallback"
    assert event.get("decision") == "allowed"
    assert event.get("reason") == "symbolic_fallback_clean"


def test_hard_cap_symbolic_fallback_blocks_on_rule_violation(tmp_path):
    """Hard cap + rule-engine deny blocks when RC_MODE=copilot."""
    rules_dir = tmp_path / ".reasoning-core"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_yaml = rules_dir / "rules.yaml"
    rules_yaml.write_text(
        "corpus_version: v1\n"
        "rules:\n"
        "  - id: no_os_import\n"
        "    type: forbid_import\n"
        "    severity: deny\n"
        "    language: python\n"
        "    target: os\n"
        "    message: os import is forbidden\n",
        encoding="utf-8",
    )
    src = tmp_path / "x.py"
    src.write_text("# module\n", encoding="utf-8")
    with _stub(delay_s=3.0) as stub:
        proc = _run_hook(
            _edit_payload(str(src), "# module", "import os\n# module"),
            env={
                "S2_URL": stub.url(),
                "S2_HARD_CAP_MS": "500",
                "RC_RULE_ENGINE": "1",
                "RC_RULE_ENGINE_ALLOW_BASIC_YAML": "1",
                "RC_PROJECT_DIR": str(tmp_path),
                "RC_MODE": "copilot",
            },
            timeout=20.0,
        )
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "rule_engine" in proc.stderr.lower() or "os import" in proc.stderr.lower()


def test_hard_cap_symbolic_fallback_advise_mode_warns(tmp_path):
    """Hard cap + rule-engine deny warns when RC_MODE=advise (default)."""
    rules_dir = tmp_path / ".reasoning-core"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_yaml = rules_dir / "rules.yaml"
    rules_yaml.write_text(
        "corpus_version: v1\n"
        "rules:\n"
        "  - id: no_os_import\n"
        "    type: forbid_import\n"
        "    severity: deny\n"
        "    language: python\n"
        "    target: os\n"
        "    message: os import is forbidden\n",
        encoding="utf-8",
    )
    src = tmp_path / "x.py"
    src.write_text("# module\n", encoding="utf-8")
    with _stub(delay_s=3.0) as stub:
        proc = _run_hook(
            _edit_payload(str(src), "# module", "import os\n# module"),
            env={
                "S2_URL": stub.url(),
                "S2_HARD_CAP_MS": "500",
                "RC_RULE_ENGINE": "1",
                "RC_RULE_ENGINE_ALLOW_BASIC_YAML": "1",
                "RC_PROJECT_DIR": str(tmp_path),
            },
            timeout=20.0,
        )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "rule_engine" in proc.stderr.lower() or "os import" in proc.stderr.lower()


def test_hard_cap_helpers_isolated():
    """Importable helper functions return sane values."""
    import importlib
    sys.path.insert(0, os.path.join(REPO_ROOT, "src", "hooks"))
    try:
        if "pre_edit_guard" in sys.modules:
            mod = importlib.reload(sys.modules["pre_edit_guard"])
        else:
            mod = importlib.import_module("pre_edit_guard")
        # Default hard cap = 1500ms = 1.5s.
        assert mod._hard_cap_seconds() == pytest.approx(1.5)
        # Effective timeout = min(hard cap, S2_TIMEOUT).
        assert mod._effective_score_timeout() == pytest.approx(1.5)
    finally:
        sys.path.remove(os.path.join(REPO_ROOT, "src", "hooks"))
