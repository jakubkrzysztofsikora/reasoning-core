"""Tests for the dup-advisory findings ledger (src/hooks/_dup_findings.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.hooks import _dup_findings as ledger


def _finding(name="onDocClick", **over):
    f = ledger.make_finding(
        added_path="src/AccountMenu.ts",
        added_name=name,
        matches=[
            {"path": "src/SortMenu.ts", "name": "onDocClick", "lineno": 99, "logic": 1.0},
            {"path": "src/BasemapControl.ts", "name": "onDocClick", "lineno": 160, "logic": 0.98},
        ],
        verdict="extract",
        in_diff=False,
        likely_move=False,
        test_covered=True,
    )
    f.update(over)
    return f


def test_ledger_path_is_per_repo_under_cache_dir(tmp_path):
    a = ledger.ledger_path("/repo/a", cache_dir=str(tmp_path))
    b = ledger.ledger_path("/repo/b", cache_dir=str(tmp_path))
    assert a != b
    assert a.parent == tmp_path


def test_finding_id_is_stable_and_ignores_match_order():
    f1 = _finding()
    f2 = ledger.make_finding(
        added_path=f1["added_path"],
        added_name=f1["added_name"],
        matches=list(reversed(f1["matches"])),
        verdict="extract",
        in_diff=False,
        likely_move=False,
        test_covered=True,
    )
    assert f1["id"] == f2["id"]
    assert f1["sites"] == 3
    assert f1["status"] == "open"


def test_record_then_load_round_trips_and_latest_row_wins(tmp_path):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, f)
    ledger.record(path, dict(f, verdict="note"))
    rows = ledger.load(path)
    assert list(rows) == [f["id"]]
    assert rows[f["id"]]["verdict"] == "note"


def test_load_missing_ledger_is_empty(tmp_path):
    assert ledger.load(tmp_path / "nope.jsonl") == {}


def test_set_status_deferred_requires_reason_and_known_id(tmp_path):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, f)
    with pytest.raises(ValueError):
        ledger.set_status(path, f["id"], "deferred", reason="  ")
    with pytest.raises(KeyError):
        ledger.set_status(path, "deadbeef", "deferred", reason="later")
    ledger.set_status(path, f["id"], "deferred", reason="couples suites")
    row = ledger.load(path)[f["id"]]
    assert row["status"] == "deferred" and row["reason"] == "couples suites"


def test_record_does_not_reopen_a_deferred_finding(tmp_path):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, f)
    ledger.set_status(path, f["id"], "deferred", reason="tracked elsewhere")
    ledger.record(path, _finding())  # the same dup is seen again on a later edit
    assert ledger.load(path)[f["id"]]["status"] == "deferred"


def test_record_write_failure_raises_with_cause(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ledger.LedgerError) as ei:
        ledger.record(blocker / "sub" / "ledger.jsonl", _finding())
    assert ei.value.__cause__ is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_changed_files_reports_modified_and_untracked_relative_paths(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.ts").write_text("a\n")
    (tmp_path / "src" / "edited.ts").write_text("a\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    assert ledger.changed_files(str(tmp_path)) == set()

    (tmp_path / "src" / "edited.ts").write_text("b\n")
    (tmp_path / "src" / "new.ts").write_text("c\n")
    assert ledger.changed_files(str(tmp_path)) == {"src/edited.ts", "src/new.ts"}


def test_changed_files_outside_git_is_unknown_and_says_so(tmp_path, capsys):
    assert ledger.changed_files(str(tmp_path)) is None
    assert "unknown" in capsys.readouterr().err


def test_corrupt_line_is_reported_not_silently_dropped(tmp_path, capsys):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    ledger.record(path, _finding())
    with path.open("a") as fh:
        fh.write("{not json\n")
    rows = ledger.load(path)
    assert len(rows) == 1
    assert "corrupt" in capsys.readouterr().err
