"""Tests for the host-agnostic env shim (Phase 0.5)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.hooks import _host_env  # noqa: E402


def test_explicit_rc_host(monkeypatch):
    monkeypatch.setenv("RC_HOST", "gemini")
    monkeypatch.setenv("GEMINI_PROJECT_DIR", "/tmp/g")
    assert _host_env.host() == "gemini"
    assert str(_host_env.project_dir()) == "/tmp/g"


def test_unknown_when_no_envs(monkeypatch):
    for k in ("RC_HOST", "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID",
              "GEMINI_PROJECT_DIR", "GEMINI_SESSION_ID",
              "COPILOT_PROJECT_DIR", "COPILOT_SESSION_ID",
              "VIBE_PROJECT_DIR", "VIBE_SESSION_ID",
              "RC_PROJECT_DIR", "RC_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    assert _host_env.host() == "unknown"


def test_single_host_match(monkeypatch):
    for k in ("RC_HOST", "CLAUDE_PROJECT_DIR", "GEMINI_PROJECT_DIR",
              "COPILOT_PROJECT_DIR", "VIBE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VIBE_PROJECT_DIR", "/tmp/v")
    assert _host_env.host() == "vibe"


def test_ambiguous_raises_when_no_rc_host(monkeypatch):
    for k in ("RC_HOST",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/c")
    monkeypatch.setenv("VIBE_PROJECT_DIR", "/tmp/v")
    with pytest.raises(_host_env.HostAmbiguous):
        _host_env.host()


def test_session_id_synthesises_anon_prefix(monkeypatch):
    for k in ("RC_SESSION_ID", "CLAUDE_SESSION_ID", "GEMINI_SESSION_ID",
              "COPILOT_SESSION_ID", "VIBE_SESSION_ID", "RC_HOST"):
        monkeypatch.delenv(k, raising=False)
    sid = _host_env.session_id()
    assert sid.startswith("anon-") and len(sid) > 5


def test_concurrency_audit_log_no_corruption(tmp_path, monkeypatch):
    """16 procs append simultaneously; portalocker prevents row corruption."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("RC_HOST", "claude")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "concurrency-test")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Each row is >8KB so it exceeds PIPE_BUF; without portalocker the
    # write() syscall is NOT atomic and rows would interleave. This test
    # actually exercises the lock path (not just well-behaved small writes).
    script = (
        "import sys, os\n"
        "sys.path.insert(0, %r)\n"
        "from src.hooks import audit_log\n"
        "filler = 'X' * 9000\n"
        "for i in range(20):\n"
        "    audit_log.append_event({"
        "'tool_name':'Edit','decision':'allow','i':i,'pid':os.getpid(),'big':filler}, fsync=True)\n"
    ) % str(REPO)

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            env={**os.environ},
        )
        for _ in range(16)
    ]
    for p in procs:
        assert p.wait(timeout=30) == 0

    jsonl_files = list(tmp_path.rglob("*.jsonl"))
    assert jsonl_files, "no audit jsonl produced"
    total = 0
    for f in jsonl_files:
        for raw in f.read_text().splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)  # would raise on torn write
            assert "host" in row
            assert "schema_version" in row
            total += 1
    assert total == 16 * 20, f"expected 320 rows, got {total}"
