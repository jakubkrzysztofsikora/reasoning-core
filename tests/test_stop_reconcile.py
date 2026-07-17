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


def _run_hook(project_dir: Path, env_extra: dict) -> subprocess.CompletedProcess:
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
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input="{}",
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_stop_hook_advisory_when_clean(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    # No files changed
    proc = _run_hook(project, {"RC_MODE": "advise"})
    assert proc.returncode == 0, proc.stderr


def test_stop_hook_advisory_when_mcp_skip(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    # Write a file that was not gated (MCP-skip)
    (project / "orphan.py").write_text("print('hi')", encoding="utf-8")

    proc = _run_hook(project, {"RC_MODE": "advise", "RC_SESSION_ID": "test"})
    # Advisory mode: exit 0, just warn
    assert proc.returncode == 0, proc.stderr
    assert "MCP-SKIP" in proc.stderr or "mcp-skip" in proc.stderr.lower() or "advisory" in proc.stderr.lower()


def test_stop_hook_copilot_blocks_mcp_skip(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    # Write a file that was not gated (MCP-skip)
    (project / "orphan.py").write_text("print('hi')", encoding="utf-8")

    proc = _run_hook(project, {"RC_MODE": "copilot", "RC_SESSION_ID": "test"})
    # Copilot mode: exit 2
    assert proc.returncode == 2, proc.stderr
    assert "MCP-SKIP" in proc.stderr