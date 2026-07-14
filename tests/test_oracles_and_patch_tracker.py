"""Tests for Phase 2 execution-grounded oracles and patch tracker."""
from __future__ import annotations

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

import _oracles  # type: ignore
import _patch_tracker  # type: ignore


# ---------------------------------------------------------------------------
# Oracle tests
# ---------------------------------------------------------------------------


def test_py_compile_passes_for_valid_python():
    report = _oracles.run_oracles(
        "src/foo.py",
        "def f():\n    return 1\n",
        enable_t1=True,
        enable_t2=False,
    )
    assert report.clean is True
    assert report.annotations == []


def test_py_compile_fails_for_syntax_error():
    report = _oracles.run_oracles(
        "src/foo.py",
        "def f(\n    return 1\n",
        enable_t1=True,
        enable_t2=False,
    )
    assert report.clean is False
    assert any(a.tool == "py_compile" for a in report.annotations)
    err = report.first_error()
    assert err is not None
    assert err.file_path == "src/foo.py"


def test_oracles_skip_non_python():
    report = _oracles.run_oracles(
        "src/foo.ts",
        "const x = 1;\n",
        enable_t1=True,
        enable_t2=False,
    )
    assert report.clean is True


def test_ruff_oracle_skips_bash(tmp_path):
    """Regression: ruff is a Python linter and must not parse .sh files.

    2026-07-14 incident -- the pre_edit_guard ran ruff on a bash hook
    script (e.g. client/hooks/hive-inbox-inject.sh), ruff treated the
    content as Python, emitted a parse error ("Simple statements must
    be separated by newlines or semicolons"), and the gate hard-blocked
    the edit. The fix is to mirror the T1 oracles and skip any non-".py"
    file in _t2_ruff.

    Only run if ruff is on PATH -- this test still passes without it
    because the early return is the same code path, but we mark skip
    so the CI signal is unambiguous.
    """
    import shutil

    if not shutil.which("ruff"):
        pytest.skip("ruff not on PATH; oracle is a no-op anyway")

    report = _oracles.run_oracles(
        "client/hooks/hive-inbox-inject.sh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'echo "hello"\n',
        enable_t1=True,
        enable_t2=True,
    )
    assert report.clean is True, (
        f"ruff oracle must skip .sh; got annotations={report.annotations}"
    )
    assert not any(a.tool == "ruff" for a in report.annotations)


def test_oracle_report_summary():
    report = _oracles.OracleReport()
    assert report.summary() == "oracles passed"
    report.add(tool="x", file_path="f.py", line=3, message="bad")
    assert "f.py:3" in report.summary()


# ---------------------------------------------------------------------------
# Patch tracker tests
# ---------------------------------------------------------------------------


def test_session_key_prefers_claude_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-123")
    monkeypatch.delenv("RC_CACHE_DIR", raising=False)
    key = _patch_tracker._session_key(str(tmp_path))
    assert key == "sess-123"


def test_scratch_dir_uses_rc_cache_dir(monkeypatch, tmp_path):
    scratch_parent = tmp_path / "scratch"
    monkeypatch.setenv("RC_CACHE_DIR", str(scratch_parent))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-key")
    d = _patch_tracker.scratch_dir(str(tmp_path))
    assert d.parent == scratch_parent / "rc-scratch"
    assert d.name == "test-key"
    assert d.exists()
    # Mode 0700
    assert oct(d.stat().st_mode)[-3:] == "700"


def test_append_edit_creates_cumulative_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "append-test")
    project = tmp_path / "project"
    project.mkdir()

    patch_path = _patch_tracker.append_edit(
        project_root=str(project),
        file_path="src/foo.py",
        before_src="def f(): pass\n",
        after_src="def f():\n    return 1\n",
    )
    assert patch_path.exists()
    body = patch_path.read_text(encoding="utf-8")
    assert "--- a/src/foo.py" in body
    assert "+++ b/src/foo.py" in body
    assert "+def f():" in body

    # Second append adds another hunk.
    _patch_tracker.append_edit(
        project_root=str(project),
        file_path="src/bar.py",
        before_src="x = 1\n",
        after_src="x = 2\n",
    )
    body = patch_path.read_text(encoding="utf-8")
    assert body.count("--- a/") == 2


def test_reset_pending_patch_clears_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "reset-test")
    project = tmp_path / "project"
    project.mkdir()
    _patch_tracker.append_edit(
        project_root=str(project),
        file_path="src/foo.py",
        before_src="a",
        after_src="b",
    )
    _patch_tracker.reset_pending_patch(str(project))
    patch_path = _patch_tracker.pending_patch_path(str(project))
    assert patch_path.read_text(encoding="utf-8") == ""


@pytest.mark.skipif(
    os.system("git --version > /dev/null 2>&1") != 0,
    reason="git not available",
)
def test_run_in_worktree_for_git_repo(tmp_path, monkeypatch):
    """End-to-end: create a git repo, append an edit, and apply it in a worktree."""
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "worktree-test")
    project = tmp_path / "project"
    project.mkdir()

    # Initialize a git repo with one file.
    import subprocess

    subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(project),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(project),
        check=True,
        capture_output=True,
    )
    (project / "src").mkdir()
    (project / "src" / "foo.py").write_text("def f(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(project), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(project),
        check=True,
        capture_output=True,
    )

    patch_path = _patch_tracker.append_edit(
        project_root=str(project),
        file_path="src/foo.py",
        before_src="def f(): pass\n",
        after_src="def f():\n    return 1\n",
    )
    wt, err = _patch_tracker.run_in_worktree(
        project_root=str(project),
        patch_text=patch_path.read_text(encoding="utf-8"),
    )
    assert wt is not None, err
    assert err == ""
    patched = (wt / "src" / "foo.py").read_text(encoding="utf-8")
    assert "return 1" in patched


def test_run_in_worktree_returns_none_for_non_git(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "nongit-test")
    project = tmp_path / "project"
    project.mkdir()
    wt, err = _patch_tracker.run_in_worktree(str(project), patch_text="")
    assert wt is None
    assert "not a git repository" in err
