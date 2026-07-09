"""Tests for rc bypass-next / confirm-next operator audit events."""
from __future__ import annotations

import datetime as _dt
import importlib
import json
import os
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


def test_bypass_next_arms_and_emits_operator_override(isolated_rc):
    tmp_path, rc_cli, _al, ks = isolated_rc
    rc = rc_cli.main(["bypass-next"])
    assert rc == 0
    assert ks.snapshot().get("bypass_next") is True
    ev = _read_latest_event(tmp_path / "events")
    assert ev is not None
    assert ev["tool_name"] == "rc"
    assert ev["decision"] == "operator_override"
    assert ev["reason"] == "bypass_next_armed"
    assert ev["session_id"] == "rc-cli-test"
    assert "decision_id" in ev


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
