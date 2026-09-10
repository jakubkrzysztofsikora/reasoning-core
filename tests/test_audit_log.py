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


def test_correlation_fields_capture_real_session_join_keys(isolated_audit):
    target = Path(REPO_ROOT) / "src" / "example.py"
    fields = audit_log.correlation_fields(
        {
            "cwd": REPO_ROOT,
            "session_id": "session-test-fixture",
            "run_id": "run-123",
            "task_id": "task-456",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_call_id": "tool-789",
            "turn_index": "12",
            "baseline_id": "baseline-2026-09-09",
        },
        file_path=str(target),
        before_src="old\n",
        after_src="new\n",
    )

    assert fields["correlation_schema_version"] == 1
    assert fields["run_id"] == "run-123"
    assert fields["task_id"] == "task-456"
    assert fields["transcript_path"] == "/tmp/transcript.jsonl"
    assert fields["tool_call_id"] == "tool-789"
    assert fields["turn_index"] == 12
    assert fields["file_path_rel"] == "src/example.py"
    assert fields["before_sha256"] == audit_log._sha256_text("old\n")
    assert fields["after_sha256"] == audit_log._sha256_text("new\n")
    assert len(fields["git_head_before"]) == 40
    assert fields["git_head"] == fields["git_head_before"]
    assert fields["baseline_id"] == "baseline-2026-09-09"


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


def test_redaction_clears_relative_path_and_source_hashes(isolated_audit):
    ev = audit_log.new_event(
        tool_name="Edit",
        decision="allowed",
        file_path="/x/.env",
        file_path_rel=".env",
        before_sha256="before",
        after_sha256="after",
    )
    audit_log.append_event(ev)
    rec = _read_session_lines(isolated_audit)[-1]
    assert rec["file_path"] == "[REDACTED]"
    assert rec["file_path_rel"] == "[REDACTED]"
    assert rec["before_sha256"] is None
    assert rec["after_sha256"] is None


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


# ---------------------------------------------------------------------------
# U4 (2026-06-01): gate_id plumbing
# ---------------------------------------------------------------------------

def test_gate_id_validation_known(isolated_audit):
    ev = audit_log.new_event(
        tool_name="Edit",
        decision="allowed",
        gate_id="scorer",
    )
    assert ev.get("gate_id") == "scorer"


def test_gate_id_validation_unknown_stripped(isolated_audit):
    ev = audit_log.new_event(
        tool_name="Edit",
        decision="allowed",
        gate_id="not_a_real_gate",
    )
    assert "gate_id" not in ev


def test_gate_id_omitted_when_none(isolated_audit):
    ev = audit_log.new_event(tool_name="Edit", decision="allowed")
    assert "gate_id" not in ev


def test_record_operator_override_writes_event(isolated_audit):
    audit_log.record_operator_override(reason="test_override")
    lines = _read_session_lines(isolated_audit)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["tool_name"] == "rc"
    assert rec["decision"] == "operator_override"
    assert rec["reason"] == "test_override"


def test_record_operator_confirmed_writes_event(isolated_audit):
    audit_log.record_operator_confirmed(reason="test_confirm")
    lines = _read_session_lines(isolated_audit)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["tool_name"] == "rc"
    assert rec["decision"] == "operator_confirmed"
    assert rec["reason"] == "test_confirm"
