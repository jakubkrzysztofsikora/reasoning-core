"""Tests for rc bypass-next / confirm-next operator audit events."""
from __future__ import annotations

import datetime as _dt
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture
def isolated_rc(tmp_path, monkeypatch):
    """Isolate rc CLI state and audit log to tmp_path."""
    state_dir = tmp_path / "state"
    audit_dir = tmp_path / "events"
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_dir))
    monkeypatch.setenv("RC_SESSION_ID", "rc-cli-test")
    # Reload modules so module-level paths pick up the new env.
    import _kill_switches as ks
    import audit_log as al
    import rc_cli
    importlib.reload(ks)
    importlib.reload(al)
    importlib.reload(rc_cli)
    return tmp_path, rc_cli, al, ks


def _today_dir(root: Path) -> Path:
    day = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    return root / day


def _read_latest_event(audit_dir: Path):
    day_dir = _today_dir(audit_dir)
    files = sorted(day_dir.glob("*.jsonl"))
    if not files:
        return None
    lines = [json.loads(line) for line in files[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-1] if lines else None


def test_bypass_next_arms_and_emits_operator_override(isolated_rc, monkeypatch):
    """Round-3 fix: ``bypass-next`` requires operator authentication.

    The original round-2 test called ``bypass-next`` without auth,
    which round-3 identified as a BLOCKER (the agent could self-arm
    the bypass and rewrite a guarded file in a single round-trip).
    This test now exercises the operator-authenticated path, mirrors
    the ``_auth_env`` fixture in ``test_rc_cli_enable_enforcement``,
    and asserts that an unauthenticated call (the new test below)
    refuses.
    """
    tmp_path, rc_cli, _al, ks = isolated_rc
    
    # Mock subprocess.run for sudo check and keychain lookup (RC-SEC-02)
    token = "test-token-for-operator-events-bypass-next-aaaaaa1234"
    def fake_run(cmd, *args, **kwargs):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, returncode=0)
        if isinstance(cmd, list) and "find-generic-password" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=token,
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", token)
    rc = rc_cli.main(["bypass-next"])
    assert rc == 0, f"expected authenticated bypass-next to succeed; got rc={rc}"
    assert ks.snapshot().get("bypass_next") is True
    ev = _read_latest_event(tmp_path / "events")
    assert ev is not None
    assert ev["tool_name"] == "rc"
    assert ev["decision"] == "operator_override"
    assert ev["reason"] == "bypass_next_armed"
    assert ev["session_id"] == "rc-cli-test"
    assert "decision_id" in ev


def test_bypass_next_refuses_without_operator_auth(isolated_rc):
    """Round-3 BLOCKER: unauthenticated ``bypass-next`` must refuse.

    Without operator auth, ``bypass-next`` must return 1 and the
    kill-switch state must remain un-armed. This closes the
    round-2 Finding 6 vector where an agent could arm the bypass
    via ``python3 src/rc_cli.py bypass-next``.
    """
    tmp_path, rc_cli, _al, ks = isolated_rc
    monkeypatch_delenv = __import__("os").environ.copy()
    for k in ("RC_ENFORCEMENT_TOKEN",):
        monkeypatch_delenv.pop(k, None)
    rc = rc_cli.main(["bypass-next"])
    assert rc == 1, (
        f"BLOCKER: unauthenticated bypass-next must fail; got rc={rc}"
    )
    # ``ks`` was loaded before monkeypatch delenv above; reload to read
    # the post-call state from disk.
    import importlib
    import _kill_switches as ks_mod
    importlib.reload(ks_mod)
    assert ks_mod.snapshot().get("bypass_next", False) is False, (
        "BLOCKER: bypass-next armed the kill switch without auth."
    )


def test_confirm_next_emits_operator_confirmed(isolated_rc):
    tmp_path, rc_cli, _al, _ks = isolated_rc
    rc = rc_cli.main(["confirm-next"])
    assert rc == 0
    ev = _read_latest_event(tmp_path / "events")
    assert ev is not None
    assert ev["tool_name"] == "rc"
    assert ev["decision"] == "operator_confirmed"
    assert ev["reason"] == "confirm_next_armed"
    assert ev["session_id"] == "rc-cli-test"
    assert "decision_id" in ev


def test_record_verification_emits_correlated_result(isolated_rc):
    tmp_path, rc_cli, _al, _ks = isolated_rc
    project = tmp_path / "project"
    project.mkdir()
    rc = rc_cli.main(
        [
            "record-verification",
            "--kind",
            "test",
            "--status",
            "failed",
            "--exit-code",
            "1",
            "--command",
            "pytest -q tests/test_example.py",
            "--decision-id",
            "decision-123",
            "--project-dir",
            str(project),
            "--session-id",
            "rc-cli-test",
            "--run-id",
            "run-123",
            "--task-id",
            "task-456",
            "--transcript-path",
            "/tmp/transcript.jsonl",
            "--tool-call-id",
            "tool-789",
            "--turn-index",
            "8",
            "--baseline-id",
            "baseline-2026-09-09",
        ]
    )
    assert rc == 0
    ev = _read_latest_event(tmp_path / "events")
    assert ev is not None
    assert ev["event_type"] == "verification_recorded"
    assert ev["decision"] == "verification_failed"
    assert ev["verification_kind"] == "test"
    assert ev["verification_status"] == "failed"
    assert ev["exit_code"] == 1
    assert ev["parent_decision_id"] == "decision-123"
    assert ev["session_id"] == "rc-cli-test"
    assert ev["run_id"] == "run-123"
    assert ev["task_id"] == "task-456"
    assert ev["tool_call_id"] == "tool-789"
    assert ev["turn_index"] == 8
