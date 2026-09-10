"""Tests for conservative real-session evidence joins."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from src.hooks._session_correlator import build_ledger


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _write_events(root: Path, session_id: str, events: list[dict]) -> None:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    target = root / day
    target.mkdir(parents=True)
    (target / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_build_ledger_joins_labels_verification_outcome_and_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    target = repo / "foo.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = _commit(repo, "initial")
    target.write_text("value = 2\n", encoding="utf-8")
    after = _commit(repo, "implement change")

    audit_root = tmp_path / "events"
    decision = {
        "event_type": "decision",
        "decision_id": "decision-1",
        "session_id": "session-1",
        "project_dir": str(repo),
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "file_path": str(target),
        "file_path_rel": "foo.py",
        "decision": "allowed",
        "signal_source": "ssm",
        "git_head_before": before,
        "run_id": "run-1",
    }
    verification = {
        "event_type": "verification_recorded",
        "decision_id": "verification-1",
        "session_id": "session-1",
        "project_dir": str(repo),
        "parent_decision_id": "decision-1",
        "verification_kind": "test",
        "verification_status": "failed",
        "deterministic": True,
    }
    outcome = {
        "event_type": "session_outcome_recorded",
        "decision_id": "outcome-1",
        "session_id": "session-1",
        "project_dir": str(repo),
        "outcome_status": "unverified_gap",
        "git_head_after": after,
    }
    _write_events(audit_root, "session-1", [decision, verification, outcome])
    labels = tmp_path / "training.jsonl"
    labels.write_text(
        json.dumps({"decision_id": "decision-1", "labels": {"test_failure": True}}) + "\n",
        encoding="utf-8",
    )

    ledger = build_ledger(audit_root, training_set=labels, days=1, include_synthetic=True)

    assert ledger["summary"]["decisions"] == 1
    assert ledger["summary"]["labeled_decisions"] == 1
    assert ledger["summary"]["exact_verification_links"] == 1
    assert ledger["summary"]["allowed_or_warn_with_failed_verification"] == 1
    assert ledger["summary"]["linked_commit_decisions"] == 1
    assert ledger["summary"]["coverage"]["label_rate"] == 1.0
    assert ledger["summary"]["coverage"]["exact_verification_rate"] == 1.0
    assert ledger["summary"]["coverage"]["session_outcome_rate"] == 1.0
    assert ledger["summary"]["coverage"]["commit_link_rate"] == 1.0
    row = ledger["decisions"][0]
    assert row["evidence_grade"] == "strong"
    assert row["labels"][0]["decision_id"] == "decision-1"
    assert row["exact_verifications"][0]["verification_status"] == "failed"
    assert row["commits"][0]["link_confidence"] == "exact_head_range"
