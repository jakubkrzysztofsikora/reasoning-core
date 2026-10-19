"""Tests for `rc dup-defer` CLI."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
for p in (str(REPO_ROOT), SRC_DIR, HOOKS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rc_cli  # noqa: E402
from src.duplicate_detection import findings as ledger  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    f = ledger.make_finding(
        added_path="a.ts", added_name="f", matches=[{"path": "b.ts", "name": "g", "lineno": 1, "logic": 1.0}],
        verdict="extract", in_diff=False, likely_move=False, test_covered=True,
    )
    ledger.record(ledger.ledger_path(str(root)), f)
    return str(root), f["id"]


@pytest.fixture
def events(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(rc_cli.audit_log, "append_event", lambda e, **_: seen.append(e))
    return seen


def test_dup_defer_records_the_reason_and_an_audit_event(repo, events, capsys):
    root, fid = repo
    rc = rc_cli.main(["dup-defer", fid, "--reason", "callers diverge next sprint"])
    assert rc == 0
    row = ledger.load(ledger.ledger_path(root))[fid]
    assert row["status"] == "deferred"
    assert row["reason"] == "callers diverge next sprint"
    assert [e["decision"] for e in events] == ["dup_deferred"]
    assert events[0]["finding_id"] == fid
    assert fid in capsys.readouterr().out


def test_dup_defer_unknown_id_exits_1_and_writes_nothing(repo, events, capsys):
    root, fid = repo
    rc = rc_cli.main(["dup-defer", "nope", "--reason", "x"])
    assert rc == 1
    assert fid in capsys.readouterr().err  # lists the open ids
    assert events == []
    assert ledger.load(ledger.ledger_path(root))[fid]["status"] == "open"


def test_dup_defer_requires_a_reason(repo):
    _, fid = repo
    with pytest.raises(SystemExit):
        rc_cli.main(["dup-defer", fid])
