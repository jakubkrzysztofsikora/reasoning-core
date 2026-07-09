"""Tests for Phase 4 commit miner."""
from __future__ import annotations

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

import _commit_miner as cm  # type: ignore


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


def test_mine_empty_for_non_git_dir(tmp_path: Path):
    assert cm.mine(str(tmp_path / "not-a-repo"), n=10) == []


def test_mine_returns_commits(git_repo: Path):
    _commit(git_repo, "first", "a.txt", "one")
    commits = cm.mine(str(git_repo), n=5)
    assert len(commits) == 1
    assert commits[0].sha
    assert commits[0].message == "first"
    assert "files_changed" in commits[0].features


def test_label_positive_when_no_follow_up_fix(git_repo: Path):
    _commit(git_repo, "feature work", "a.txt", "one")
    _commit(git_repo, "more feature", "b.txt", "two")
    commits = cm.mine(str(git_repo), n=5)
    assert all(c.label == "positive" for c in commits)


def test_label_negative_after_fix_touching_same_file(git_repo: Path):
    _commit(git_repo, "feature work", "a.txt", "one")
    _commit(git_repo, "fix bug", "a.txt", "one fixed")
    commits = cm.mine(str(git_repo), n=5)
    # HEAD is the fix commit and is positive (no follow-up yet).
    # The feature commit is negative.
    labels = {c.message: c.label for c in commits}
    assert labels["fix bug"] == "positive"
    assert labels["feature work"] == "negative"


def test_extract_features_counts_files_and_lines():
    commit = cm.MinedCommit(
        message="refactor test",
        files=["a.py", "b.py"],
        diff_stat={"a.py": 10, "b.py": 20},
    )
    features = cm.extract_features(commit)
    assert features["files_changed"] == 2
    assert features["lines_changed"] == 30
    assert features["has_refactor_keyword"] is True
    assert features["has_test_keyword"] is True
    assert features["has_fix_keyword"] is False
