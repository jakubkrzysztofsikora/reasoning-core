"""Deferring a duplicate finding (src/duplicate_detection/defer.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection import findings as ledger  # noqa: E402
from src.duplicate_detection.defer import DeferError, defer  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    f = ledger.make_finding(
        added_path="a.ts", added_name="f", matches=[{"path": "b.ts", "name": "g", "lineno": 1, "logic": 1.0}],
        verdict="extract", in_diff=False, likely_move=False, test_covered=True,
    )
    ledger.record(ledger.ledger_path(str(root)), f)
    return str(root), f["id"]


def test_defer_marks_the_finding_with_its_reason(repo):
    root, fid = repo
    row = defer(root, fid, "menus diverge next sprint")
    assert row["status"] == "deferred"
    assert ledger.load(ledger.ledger_path(root))[fid]["reason"] == "menus diverge next sprint"


def test_empty_reason_is_rejected(repo):
    root, fid = repo
    with pytest.raises(DeferError, match="reason"):
        defer(root, fid, "   ")


def test_unknown_id_is_rejected_and_lists_open_ids(repo):
    root, fid = repo
    with pytest.raises(DeferError) as exc:
        defer(root, "nope", "why")
    assert fid in str(exc.value)


def test_escalated_finding_can_still_be_deferred(repo):
    root, fid = repo
    ledger.set_status(ledger.ledger_path(root), fid, "escalated")
    assert defer(root, fid, "callers diverge")["status"] == "deferred"


def test_resolved_finding_cannot_be_deferred(repo):
    root, fid = repo
    ledger.set_status(ledger.ledger_path(root), fid, "resolved")
    with pytest.raises(DeferError, match="resolved"):
        defer(root, fid, "why")
