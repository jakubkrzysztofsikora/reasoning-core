"""Tests for src/hooks/audit_log.py — schema, redaction, retry markers."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402


@pytest.fixture
def isolated_audit(monkeypatch, tmp_path):
    """Redirect /tmp/rc-events to a tmp dir for the duration of the test."""
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "session-test-fixture")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(REPO_ROOT))
    yield tmp_path


def _read_session_lines(root: Path, sid: str = "session-test-fixture"):
    # File path follows the date layout.
    import datetime as _dt

    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    path = root / today / f"{sid}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_new_event_schema_minimal(isolated_audit):
    ev = audit_log.new_event(tool_name="Edit", decision="allowed", file_path="/tmp/x.py")
    assert ev["tool_name"] == "Edit"
    assert ev["decision"] == "allowed"
    assert ev["file_path"] == "/tmp/x.py"
    assert ev["session_id"] == "session-test-fixture"
    assert isinstance(ev["ts"], str) and ev["ts"].endswith("Z")
    assert "project_dir" in ev


def test_append_event_writes_jsonl(isolated_audit):
    ev = audit_log.new_event(
        tool_name="Edit",
        decision="allowed",
        file_path="/tmp/x.py",
        ais=0.91,
        coherence_delta=0.3,
        regression_detected=False,
        latency_ms=120,
    )
    audit_log.append_event(ev)
    lines = _read_session_lines(isolated_audit)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["tool_name"] == "Edit"
    assert rec["ais"] == 0.91
    assert rec["regression_detected"] is False


@pytest.mark.parametrize("path", [
    "/x/.env",
    "/x/.env.production",
    "/keys/server.pem",
    "/etc/ssl/certs/private.key",
    "/secrets/db.json",
    "/creds/credential.json",
    "/tokens/access.token",
    "/users/Bob/secrets/api.txt",
])
def test_redaction_path_patterns(isolated_audit, path):
    ev = audit_log.new_event(
        tool_name="Edit",
        decision="allowed",
        file_path=path,
        before_bytes=1024,
        after_bytes=2048,
    )
    audit_log.append_event(ev)
    rec = _read_session_lines(isolated_audit)[-1]
    assert rec["file_path"] == "[REDACTED]"
    assert rec["before_bytes"] == 0
    assert rec["after_bytes"] == 0


def test_redaction_inline_secrets_in_summary(isolated_audit):
    ev = audit_log.new_event(
        tool_name="Bash",
        decision="allowed",
        command="curl -H 'Authorization: Bearer abcd1234XYZ' https://x",
        reason="key sk-aaaaaaaaaaaaaaaaaaaa exposed; password=hunter22 GHP=ghp_0123456789ABCDEFGHIJ",
    )
    audit_log.append_event(ev)
    rec = _read_session_lines(isolated_audit)[-1]
    # Bearer token, sk- key, password=, ghp_ all replaced.
    assert "Bearer abcd1234XYZ" not in rec["command"]
    assert "sk-aaaaaaaaaaaaaaaaaaaa" not in rec["reason"]
    assert "ghp_0123456789ABCDEFGHIJ" not in rec["reason"]
    assert "password=hunter22" not in rec["reason"]
    assert "[REDACTED]" in rec["command"] or "[REDACTED]" in rec["reason"]


def test_append_silent_when_unwritable(monkeypatch, tmp_path, capsys):
    """Best-effort: unwritable target must not raise into the hook."""
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", "/proc/0/forbidden/rc-events")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test")
    ev = audit_log.new_event(tool_name="Edit", decision="allowed")
    # Should not raise.
    audit_log.append_event(ev)


def test_retry_marker_within_window(isolated_audit, monkeypatch):
    file_path = "/tmp/x.py"
    fixed_now = 1_000_000.0
    audit_log.record_block(file_path, now=fixed_now)
    # Within 120s window -> retry detected.
    assert audit_log.is_retry_after_block(file_path, now=fixed_now + 30) is True
    # Outside the window -> no retry.
    assert audit_log.is_retry_after_block(file_path, now=fixed_now + 300) is False


def test_retry_marker_unknown_path_is_false(isolated_audit):
    assert audit_log.is_retry_after_block("/tmp/never.py") is False


def test_session_id_falls_back_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", str(tmp_path))
    sid = audit_log._session_id()
    assert sid.startswith("anon-") and len(sid) > 5
