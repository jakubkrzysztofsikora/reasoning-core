"""Tests for the Stop hook reconcile integration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = str(REPO_ROOT / "src" / "hooks" / "stop_reconcile.py")


def _init_repo(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(project_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(project_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project_dir), check=True, capture_output=True)


def _run_hook(project_dir: Path, env_extra: dict, payload: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for var in (
        "RC_AUDIT_ROOT", "RC_PROJECT_DIR", "RC_RUN_DIR",
        "RC_MODE", "RC_SESSION_ID", "CLAUDE_SESSION_ID",
    ):
        env.pop(var, None)
    env.update(env_extra)
    env["RC_PROJECT_DIR"] = str(project_dir)
    env["RC_RUN_DIR"] = str(project_dir)
    env["RC_AUDIT_ROOT"] = str(project_dir / "_audit")
    stdin_payload = json.dumps(payload or {})
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_stop_hook_advisory_when_clean(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    proc = _run_hook(project, {"RC_MODE": "advise"})
    assert proc.returncode == 0, proc.stderr


def test_stop_hook_advisory_when_mcp_skip(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    (project / "orphan.py").write_text("print('hi')", encoding="utf-8")

    proc = _run_hook(project, {"RC_MODE": "advise", "RC_SESSION_ID": "test"})
    # Advisory mode: exit 0, just warn to stderr
    assert proc.returncode == 0, proc.stderr
    assert "MCP-skip" in proc.stderr or "advisory" in proc.stderr.lower()


def test_stop_hook_copilot_emits_block_with_stderr(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    (project / "orphan.py").write_text("print('hi')", encoding="utf-8")

    proc = _run_hook(project, {"RC_MODE": "copilot", "RC_SESSION_ID": "test"})
    # Copilot mode: exit 2 with stderr message; stdout must be empty
    # (Claude Code Stop hook protocol: on exit 2, JSON stdout is ignored)
    assert proc.returncode == 2, proc.stderr
    assert proc.stdout.strip() == "", f"stdout should be empty on exit 2, got: {proc.stdout!r}"
    assert "MCP-SKIP" in proc.stderr
    assert "orphan.py" in proc.stderr


def test_stop_hook_stop_hook_active_approves(tmp_path):
    """stop_hook_active=True means this is a retry — must approve to avoid loop."""
    project = tmp_path / "repo"
    _init_repo(project)
    (project / "orphan.py").write_text("print('hi')", encoding="utf-8")

    proc = _run_hook(
        project,
        {"RC_MODE": "copilot", "RC_SESSION_ID": "test"},
        payload={"stop_hook_active": True},
    )
    # Must approve on retry, even in copilot mode
    assert proc.returncode == 0, proc.stderr


def test_stop_hook_uses_payload_cwd(tmp_path):
    """When payload provides cwd, it takes precedence over env."""
    project = tmp_path / "repo"
    _init_repo(project)
    # No orphan file
    proc = _run_hook(
        project,
        {"RC_MODE": "copilot", "RC_SESSION_ID": "test", "RC_PROJECT_DIR": "/nonexistent", "RC_RUN_DIR": "/nonexistent"},
        payload={"cwd": str(project)},
    )
    assert proc.returncode == 0, proc.stderr


def test_stop_hook_infrastructure_error_does_not_block(tmp_path):
    """If rc reconcile fails for infra reasons, log to stderr but don't block."""
    project = tmp_path / "no_git"
    project.mkdir()
    # No .git directory
    proc = _run_hook(project, {"RC_MODE": "copilot", "RC_SESSION_ID": "test"})
    # Non-git dir → reconcile returns empty → no MCP-skip → exit 0
    assert proc.returncode == 0, proc.stderr