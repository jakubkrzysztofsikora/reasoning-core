"""Tests for `rc enable-enforcement` first-run wizard."""
from __future__ import annotations

import importlib
import os
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
def isolated_project(tmp_path, monkeypatch):
    """Project dir with README.md and isolated rc CLI state."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text(
        "# My Project\n\nThis project does the cool thing.\n",
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


def test_enable_enforcement_scaffolds_plan_and_envrc_local(isolated_project):
    project_dir, rc_cli = isolated_project
    rc = rc_cli.main(["enable-enforcement"])
    assert rc == 0

    plan = project_dir / "PLAN.md"
    assert plan.exists()
    body = plan.read_text(encoding="utf-8")
    assert "# PLAN" in body
    assert "This project does the cool thing." in body

    envrc_local = project_dir / ".envrc.local"
    assert envrc_local.exists()
    local_body = envrc_local.read_text(encoding="utf-8")
    assert "RC_MODE=copilot" in local_body
    assert "RC_SHADOW_MODE=0" in local_body
    assert "RC_PLAN_BLOCK=1" in local_body


def test_enable_enforcement_is_idempotent(isolated_project):
    project_dir, rc_cli = isolated_project
    assert rc_cli.main(["enable-enforcement"]) == 0
    first_plan = (project_dir / "PLAN.md").read_text(encoding="utf-8")
    first_local = (project_dir / ".envrc.local").read_text(encoding="utf-8")

    assert rc_cli.main(["enable-enforcement"]) == 0
    second_plan = (project_dir / "PLAN.md").read_text(encoding="utf-8")
    second_local = (project_dir / ".envrc.local").read_text(encoding="utf-8")

    assert second_plan == first_plan
    assert second_local == first_local
    assert second_local.count("RC_MODE=copilot") == 1
