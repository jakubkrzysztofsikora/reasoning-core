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


def _auth_env(monkeypatch, token: str = "operator-secret-token-1234567890ab"):
    """Simulate authenticated operator environment with keychain match."""
    import hashlib
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", token)
    # On non-darwin (CI), auth requires RC_AUTH_TOKEN_HASH matching the token
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.setenv("RC_AUTH_TOKEN_HASH", token_hash)
    # Mock subprocess.run to handle both sudo check and keychain lookup
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
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "wrong-token-but-long-enough-1234567890")
    # Mock subprocess.run to handle sudo check and return different keychain token
    def fake_run(cmd, *args, **kwargs):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, returncode=0)
        if isinstance(cmd, list) and "find-generic-password" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="different-stored-secret",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
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

# ---------------------------------------------------------------------------
# Round-3 Finding: ``rc bypass-next`` is unauthenticated.
#
# An agent running ``python3 src/rc_cli.py bypass-next`` can arm the
# bypass without operator authentication. The next PreToolUse hook
# then exits 0 unconditionally, letting the agent write through the
# guard. The bash guard already blocks the shell form
# (``pre_bash_guard.py`` regex catches ``python3 ... rc_cli bypass-next``)
# but the path-form (``python3 src/rc_cli.py bypass-next``) is
# unrestricted in ``cmd_bypass_next`` itself. This test guards the fix:
# ``rc bypass-next`` must require the same operator authentication as
# ``rc enable-enforcement``.
# ---------------------------------------------------------------------------


def test_bypass_next_requires_operator_authentication(isolated_project, monkeypatch):
    """Agent invoking ``rc bypass-next`` must fail without auth."""
    project_dir, rc_cli = isolated_project
    # Ensure no auth env is set
    monkeypatch.delenv("RC_ENFORCEMENT_TOKEN", raising=False)
    rc = rc_cli.main(["bypass-next"])
    assert rc == 1, (
        f"BLOCKER: rc bypass-next succeeded without operator auth; "
        f"the next PreToolUse hook will exit 0 unconditionally."
    )
    # The kill-switch state must remain un-armed.
    import _kill_switches as ks
    importlib.reload(ks)
    assert ks.consume_bypass_next() is False, (
        "BLOCKER: rc bypass-next armed the kill switch without auth."
    )


def test_bypass_next_succeeds_with_operator_authentication(isolated_project, monkeypatch):
    """Operator with valid auth token can arm bypass-next."""
    project_dir, rc_cli = isolated_project
    _auth_env(monkeypatch)
    rc = rc_cli.main(["bypass-next"])
    assert rc == 0, f"expected success with auth, got rc={rc}"
    import _kill_switches as ks
    importlib.reload(ks)
    assert ks.consume_bypass_next() is True, (
        "rc bypass-next with valid auth must arm the kill switch."
    )
