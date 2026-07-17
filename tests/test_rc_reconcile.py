"""Tests for rc reconcile safety net."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture
def fresh_rc_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))
    import rc_cli
    importlib.reload(rc_cli)
    return rc_cli


def _write_event(audit_root: Path, file_path: str, decision: str = "allowed"):
    import datetime as _dt
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    day_dir = audit_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    log = day_dir / "session.jsonl"
    event = {
        "ts": f"{day}T12:00:00Z",
        "decision_id": "abc123",
        "session_id": "session",
        "file_path": file_path,
        "decision": decision,
        "tool_name": "Edit",
    }
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")


def test_reconcile_flags_missing_gate_event(fresh_rc_cli, tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(project_dir), check=True, capture_output=True)
    audit_root = tmp_path / "events"

    # Write a file that was not gated (no gate_edit audit row)
    (project_dir / "orphan.py").write_text("print('hello')", encoding="utf-8")

    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_root))
    monkeypatch.setenv("RC_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("RC_SESSION_ID", "session")

    missing = fresh_rc_cli._reconcile_missing_gate_events(str(project_dir), str(audit_root), "session")
    assert missing == ["orphan.py"]


def test_reconcile_ignores_gated_files(fresh_rc_cli, tmp_path, monkeypatch):
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(project_dir), check=True, capture_output=True)
    audit_root = tmp_path / "events"

    (project_dir / "gated.py").write_text("print('hello')", encoding="utf-8")
    _write_event(audit_root, "gated.py")

    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_root))
    monkeypatch.setenv("RC_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("RC_SESSION_ID", "session")

    missing = fresh_rc_cli._reconcile_missing_gate_events(str(project_dir), str(audit_root), "session")
    assert missing == []
