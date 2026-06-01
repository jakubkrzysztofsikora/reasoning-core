"""U10: calibration_corpus.py — positive-label rows from revert/fix commits.

Audit 2026-06-01 §10: eval/calibrated/labels.jsonl shipped with 206 rows,
100% labeled negative. Mine `fix:` and `Revert "..."` commits to add
positive-label samples (the buggy-parent-of-fix or the reverted commit).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=10,
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r.stdout


def _seed_fix_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main", check=False)
    _git(repo, "config", "user.email", "test@local")
    _git(repo, "config", "user.name", "Test")
    foo = repo / "foo.py"
    # commit 1: clean
    foo.write_text('def f(): return "hello"\n', encoding="utf-8")
    _git(repo, "add", "foo.py")
    _git(repo, "commit", "-q", "-m", "feat: initial implementation")
    # commit 2: introduce bug
    foo.write_text('def f(): return "hellobug"\n', encoding="utf-8")
    _git(repo, "add", "foo.py")
    _git(repo, "commit", "-q", "-m", "feat: enhance greeting")
    # commit 3: fix the bug
    foo.write_text('def f(): return "hello"\n', encoding="utf-8")
    _git(repo, "add", "foo.py")
    _git(repo, "commit", "-q", "-m", "fix: undo the typo in greeting")


def test_fix_commit_emits_positive_label(tmp_path):
    repo = tmp_path / "fixture"
    _seed_fix_repo(repo)
    out = tmp_path / "labels.jsonl"
    here = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(here)
    r = subprocess.run(
        [sys.executable, "-m", "eval.calibration_corpus",
         "--repo-root", str(repo), "--since", "1.year",
         "--out", str(out), "--include-positives"],
        capture_output=True, text=True, timeout=30, env=env, cwd=here,
    )
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    pos = [row for row in rows if row.get("label") == "positive"]
    assert pos, f"no positive rows found: {rows}"
    assert any(row["path"] == "foo.py" for row in pos)
    assert any(row.get("source", "").startswith("fix_parent") for row in pos)


def test_no_positives_flag_suppresses(tmp_path):
    repo = tmp_path / "fixture"
    _seed_fix_repo(repo)
    out = tmp_path / "labels.jsonl"
    here = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(here)
    r = subprocess.run(
        [sys.executable, "-m", "eval.calibration_corpus",
         "--repo-root", str(repo), "--since", "1.year",
         "--out", str(out), "--no-positives"],
        capture_output=True, text=True, timeout=30, env=env, cwd=here,
    )
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    positives = [row for row in rows if row.get("label") == "positive"]
    assert not positives, f"expected zero positives with --no-positives, got: {positives}"


def test_bisect_mode_raises():
    """bisect strategy is stubbed."""
    sys.path.insert(0, str(Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    from eval import calibration_corpus  # type: ignore
    with pytest.raises(NotImplementedError):
        calibration_corpus.mine(".", "1.year", "/tmp/_x.jsonl", mode="bisect")
