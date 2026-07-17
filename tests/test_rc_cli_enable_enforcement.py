"""Tests for `rc enable-enforcement` staged, authenticated enforcement profile."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """Project dir with README.md and isolated rc CLI state."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text(
        "# My Project\n\nThis project does the cool thing.\n",
        encoding="utf-8",
    )
    (project_dir / "PLAN.md").write_text(
        "# PLAN\n\n1. Do the cool thing.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))
    import _kill_switches as ks
    import audit_log as al
    import rc_cli
    importlib.reload(ks)
    importlib.reload(al)
    importlib.reload(rc_cli)
    return project_dir, rc_cli


def _auth_env(monkeypatch, token: str = "operator-secret-token-12345678"):
    """Simulate authenticated operator environment with keychain match."""
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", token)
    # Mock the security command to return the same token
    def fake_security(*args, **kwargs):
        result = subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout=token,
            stderr="",
        )
        return result
    monkeypatch.setattr(subprocess, "run", fake_security)


def test_enable_enforcement_requires_authentication(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    # No auth token → fail
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 1
    assert not (project_dir / ".envrc.local").exists()


def test_enable_enforcement_requires_long_token(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "short")  # < 16 chars
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 1
    assert not (project_dir / ".envrc.local").exists()


def test_enable_enforcement_rejects_wrong_token(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "wrong-token-but-long-enough")
    # Mock keychain to return a different token
    def fake_security(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout="different-stored-secret",
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_security)
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 1


def test_enable_enforcement_writes_staged_profile(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    _auth_env(monkeypatch)
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 0

    envrc_local = project_dir / ".envrc.local"
    assert envrc_local.exists()
    local_body = envrc_local.read_text(encoding="utf-8")
    assert "RC_MODE=copilot" in local_body
    assert "RC_SHADOW_MODE=0" in local_body
    assert "RC_PLAN_BLOCK=1" in local_body
    assert "RC_PLAN_GROUNDING=1" in local_body  # staged warn-only
    assert "RC_ORACLE_BLOCK=1" in local_body
    assert "RC_RULE_ENGINE=1" in local_body
    assert "RC_PROJECT_INDEX=1" in local_body
    assert "S2_FAIL_CLOSED=1" in local_body
    assert "# >>> rc enforcement >>>" in local_body
    assert "# <<< rc enforcement <<<" in local_body


def test_enable_enforcement_refuses_missing_plan(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    (project_dir / "PLAN.md").unlink()
    _auth_env(monkeypatch)
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 1
    assert not (project_dir / ".envrc.local").exists()


def test_enable_enforcement_is_idempotent(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    _auth_env(monkeypatch)
    assert rc_cli.main(["enable-enforcement"]) == 0
    first_local = (project_dir / ".envrc.local").read_text(encoding="utf-8")

    assert rc_cli.main(["enable-enforcement"]) == 0
    second_local = (project_dir / ".envrc.local").read_text(encoding="utf-8")

    assert second_local == first_local
    assert second_local.count("RC_MODE=copilot") == 1


def test_disable_enforcement_removes_only_fenced_block(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    _auth_env(monkeypatch)
    assert rc_cli.main(["enable-enforcement"]) == 0

    envrc_local = project_dir / ".envrc.local"
    envrc_local.write_text(
        envrc_local.read_text(encoding="utf-8")
        + "\n# user override\nexport CUSTOM_VAR=1\n",
        encoding="utf-8",
    )

    assert rc_cli.main(["disable-enforcement"]) == 0
    body = envrc_local.read_text(encoding="utf-8")
    assert "RC_MODE=copilot" not in body
    assert "# user override" in body
    assert "CUSTOM_VAR=1" in body


def test_enable_enforcement_hard_flag(isolated_project, monkeypatch):
    project_dir, rc_cli = isolated_project
    _auth_env(monkeypatch)
    rc = rc_cli.main(["enable-enforcement", "--hard"])
    assert rc == 0
    local_body = (project_dir / ".envrc.local").read_text(encoding="utf-8")
    assert "RC_PLAN_GROUNDING=2" in local_body