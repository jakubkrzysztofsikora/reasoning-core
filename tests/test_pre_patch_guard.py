"""Codex apply_patch bridge validates candidate files before disk mutation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "src" / "hooks" / "pre_patch_guard.py"


def _run(project: Path, patch: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "RC_PROJECT_DIR": str(project), "RC_MODE": "copilot",
           "RC_PLAN_GROUNDING": "2", "RC_PLAN_BLOCK": "1", "RC_RULE_ENGINE": "1",
           "S2_URL": "http://127.0.0.1:9", "S2_FAIL_CLOSED": "0"}
    payload = {"tool_name": "apply_patch", "tool_input": {"command": patch},
               "cwd": str(project), "session_id": "codex-patch-test"}
    return subprocess.run(
        [sys.executable, str(HOOK)], cwd=project, env=env,
        input=json.dumps(payload), capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if shutil.which("apply_patch") is None:
        pytest.skip("apply_patch binary unavailable")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_service.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy = tmp_path / ".reasoning-core"
    policy.mkdir()
    (policy / "contract.yaml").write_text(
        "version: v1\nallowed_paths:\n  - 'src/**/*.py'\n"
        "forbidden_paths:\n  - 'tests/**'\n", encoding="utf-8"
    )
    (tmp_path / "PLAN.md").write_text("# Plan\n\nEdit Python source files.\n", encoding="utf-8")
    return tmp_path


def test_allows_valid_source_patch_without_writing(project: Path):
    patch = "*** Begin Patch\n*** Update File: src/service.py\n@@\n-VALUE = 1\n+VALUE = 2\n*** End Patch\n"
    result = _run(project, patch)
    assert result.returncode == 0, result.stderr
    assert (project / "src" / "service.py").read_text() == "VALUE = 1\n"


def test_blocks_forbidden_test_patch_without_writing(project: Path):
    patch = "*** Begin Patch\n*** Update File: tests/test_service.py\n@@\n-VALUE = 1\n+VALUE = 2\n*** End Patch\n"
    result = _run(project, patch)
    assert result.returncode == 2
    assert "forbidden" in result.stderr.lower()
    assert (project / "tests" / "test_service.py").read_text() == "VALUE = 1\n"


def test_blocks_path_traversal(project: Path):
    patch = "*** Begin Patch\n*** Add File: ../escape.py\n+VALUE = 1\n*** End Patch\n"
    result = _run(project, patch)
    assert result.returncode == 2
    assert "unsafe" in result.stderr.lower()
