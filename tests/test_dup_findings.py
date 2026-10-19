"""Tests for the dup-advisory findings ledger (src/duplicate_detection/findings.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection import findings as ledger


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


def test_ledger_is_the_same_from_a_subdirectory_or_a_symlinked_path(tmp_path):
    # The hooks and `rc dup-defer` may run from different directories of one repo.
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    link = tmp_path / "link"
    link.symlink_to(repo)
    cache = str(tmp_path / "cache")
    root = ledger.ledger_path(str(repo), cache_dir=cache)
    assert ledger.ledger_path(str(repo / "src"), cache_dir=cache) == root
    assert ledger.ledger_path(str(link), cache_dir=cache) == root


def test_committed_source_tolerates_a_non_utf8_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_bytes(b"# caf\xe9\ndef f():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    assert "def f()" in ledger.committed_source(str(repo), "a.py")


def test_record_does_not_carry_blocks_from_a_resolved_finding(tmp_path):
    # Reintroduced after being fixed: a fresh cap, not the old count.
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, dict(f, blocks=2))
    ledger.set_status(path, f["id"], "resolved")
    ledger.record(path, _finding())
    assert ledger.load(path)[f["id"]].get("blocks", 0) == 0


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


def test_one_duplication_has_one_id_whichever_copy_was_edited():
    # Editing AccountMenu then SortMenu must not open a second finding (and a
    # second deferral) for the same three copies.
    a = _finding()
    b = ledger.make_finding(
        added_path="src/SortMenu.ts", added_name="onDocClick",
        matches=[
            {"path": "src/AccountMenu.ts", "name": "onDocClick", "lineno": 1, "logic": 1.0},
            {"path": "src/BasemapControl.ts", "name": "onDocClick", "lineno": 160, "logic": 0.98},
        ],
        verdict="extract", in_diff=False, likely_move=False, test_covered=True,
    )
    assert a["id"] == b["id"]


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


def test_record_keeps_the_block_count_when_the_finding_is_seen_again(tmp_path):
    # Otherwise every later edit of the file resets the Stop block cap.
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, f)
    ledger.record(path, dict(ledger.load(path)[f["id"]], blocks=2))
    ledger.record(path, _finding())
    assert ledger.load(path)[f["id"]]["blocks"] == 2


def test_record_does_not_reopen_an_escalated_finding(tmp_path):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    f = _finding()
    ledger.record(path, f)
    ledger.set_status(path, f["id"], "escalated")
    ledger.record(path, _finding())
    assert ledger.load(path)[f["id"]]["status"] == "escalated"


def test_record_skips_a_finding_that_has_not_changed(tmp_path):
    # Every edit that sees the same duplication would otherwise add a row.
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    ledger.record(path, _finding())
    rows = path.read_text().count("\n")
    ledger.record(path, _finding())
    assert path.read_text().count("\n") == rows


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


def test_valid_json_that_is_not_a_finding_is_skipped_and_reported(tmp_path, capsys):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    ledger.record(path, _finding())
    with path.open("a") as fh:
        fh.write('[1, 2]\n{"no_id": true}\n')
    assert len(ledger.load(path)) == 1
    assert capsys.readouterr().err.count("corrupt") == 2


def test_undecodable_bytes_are_skipped_and_reported(tmp_path, capsys):
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    ledger.record(path, _finding())
    with path.open("ab") as fh:
        fh.write(b"\xff\xfe garbage\n")
    assert len(ledger.load(path)) == 1
    assert "corrupt" in capsys.readouterr().err


def test_a_truncated_last_line_does_not_swallow_the_next_row(tmp_path, capsys):
    # A hook killed mid-write leaves a line with no newline; the next row must
    # still land on its own line.
    path = ledger.ledger_path("/repo", cache_dir=str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id": "x", "sta')
    f = _finding()
    ledger.record(path, f)
    assert f["id"] in ledger.load(path)
