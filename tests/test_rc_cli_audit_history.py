"""Tests for `rc audit-history` CLI."""
from __future__ import annotations

import importlib
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

import rc_cli


def _git(repo: Path, *args: str):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str, filename: str = "file.txt", content: str = "x"):
    fpath = repo / filename
    fpath.write_text(content + "\n", encoding="utf-8")
    _git(repo, "add", str(filename))
    _git(repo, "commit", "-m", message)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    return repo


@pytest.fixture
def fresh_rc_cli(monkeypatch):
    """Reload rc_cli so any module-level state picks up env changes."""
    importlib.reload(rc_cli)
    return rc_cli


def test_audit_history_table_shows_labels(git_repo: Path, fresh_rc_cli, capsys):
    _commit(git_repo, "feature", "a.txt", "one")
    _commit(git_repo, "fix bug", "a.txt", "two")
    rc = fresh_rc_cli.main(["audit-history", "--project-dir", str(git_repo), "-n", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "feature" in out
    assert "fix bug" in out
    assert "negative" in out
    assert "positive" in out


def test_audit_history_json_output(git_repo: Path, fresh_rc_cli, capsys):
    _commit(git_repo, "feature", "a.txt", "one")
    rc = fresh_rc_cli.main(["audit-history", "--project-dir", str(git_repo), "-n", "10", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"sha"' in out
    assert '"label"' in out


def test_audit_history_missing_dir(fresh_rc_cli, capsys):
    rc = fresh_rc_cli.main(["audit-history", "--project-dir", "/does/not/exist"])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err
