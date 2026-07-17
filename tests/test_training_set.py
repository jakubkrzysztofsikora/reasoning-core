"""Tests for distributed training-set label collection."""
from __future__ import annotations

import datetime as _dt
import importlib
import json
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


@pytest.fixture
def fresh_ts(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_TRAINING_SET_FILE", str(tmp_path / "training_set.jsonl"))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "audit"))
    import _training_set
    importlib.reload(_training_set)
    return _training_set


def _write_audit(audit_root: Path, rows: list[dict]) -> None:
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    day_dir = audit_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    log = day_dir / "session.jsonl"
    lines = [json.dumps(r) for r in rows]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_label_decision_id_stores_label(fresh_ts, tmp_path):
    audit_root = tmp_path / "audit"
    _write_audit(audit_root, [{
        "decision_id": "abc123",
        "session_id": "session",
        "file_path": "src/foo.py",
        "decision": "blocked",
        "signal_source": "plan_grounding",
        "before_src": "# original\n",
        "after_src": "# modified\n",
    }])

    label = fresh_ts.label_decision_id(
        "abc123",
        {"scope_drift": True, "plan_violation": True, "structural_regression": False},
        labeler_id="test",
        notes="test note",
    )
    assert label.decision_id == "abc123"
    assert label.file_path == "src/foo.py"
    assert label.session_id == "session"
    assert label.rationale_quality_failure is True
    assert label.notes == "test note"
    assert fresh_ts.store_path().exists()


def test_already_labeled(fresh_ts, tmp_path):
    audit_root = tmp_path / "audit"
    _write_audit(audit_root, [{
        "decision_id": "abc123",
        "session_id": "session",
        "file_path": "src/foo.py",
        "decision": "allowed",
    }])
    assert fresh_ts.already_labeled("abc123") is False
    fresh_ts.label_decision_id("abc123", {"scope_drift": False})
    assert fresh_ts.already_labeled("abc123") is True


def test_count_per_label(fresh_ts, tmp_path):
    audit_root = tmp_path / "audit"
    rows = [
        {"decision_id": f"id{i}", "session_id": "s", "file_path": f"f{i}.py",
         "decision": "allowed"}
        for i in range(8)
    ]
    _write_audit(audit_root, rows)

    for i, row in enumerate(rows):
        fresh_ts.label_decision_id(
            f"id{i}",
            {
                "scope_drift": i < 3,
                "plan_violation": i in (3, 4),
                "structural_regression": i == 5,
                "syntax_type_error": False,
                "test_failure": False,
            },
        )

    counts = fresh_ts.count_per_label()
    assert counts["scope_drift"] == 3
    assert counts["plan_violation"] == 2
    assert counts["structural_regression"] == 1
    assert counts["syntax_type_error"] == 0
    assert counts["test_failure"] == 0


def test_progress(fresh_ts, tmp_path):
    audit_root = tmp_path / "audit"
    rows = [
        {"decision_id": f"id{i}", "session_id": "s", "file_path": f"f{i}.py",
         "decision": "allowed"}
        for i in range(15)
    ]
    _write_audit(audit_root, rows)
    for i in range(15):
        fresh_ts.label_decision_id(
            f"id{i}", {"scope_drift": i < 12},
        )
    p = fresh_ts.progress()
    assert p["target_per_label"] == 10
    assert p["counts"]["scope_drift"] == 12
    assert p["remaining"]["scope_drift"] == 0
    assert p["remaining"]["plan_violation"] == 10
    assert p["total_stored"] == 15


def test_pick_random_unlabeled(fresh_ts, tmp_path):
    audit_root = tmp_path / "audit"
    _write_audit(audit_root, [
        {"decision_id": "id1", "session_id": "s", "file_path": "f.py",
         "decision": "allowed"},
        {"decision_id": "id2", "session_id": "s", "file_path": "g.py",
         "decision": "blocked"},
    ])
    picked = fresh_ts.pick_random_unlabeled()
    assert picked is not None
    assert picked["decision_id"] in ("id1", "id2")

    # Label one, then it should not be picked
    fresh_ts.label_decision_id(picked["decision_id"], {"scope_drift": False})
    picked2 = fresh_ts.pick_random_unlabeled()
    assert picked2 is not None
    assert picked2["decision_id"] != picked["decision_id"]


def test_should_prompt_when_target_not_met(fresh_ts, tmp_path, monkeypatch):
    monkeypatch.setattr(fresh_ts.random, "random", lambda: 0.0)
    assert fresh_ts.should_prompt_for_label() is True


def test_should_not_prompt_when_target_met(fresh_ts, tmp_path, monkeypatch):
    audit_root = tmp_path / "audit"
    rows = [
        {"decision_id": f"id{i}", "session_id": "s", "file_path": f"f{i}.py",
         "decision": "allowed"}
        for i in range(60)
    ]
    _write_audit(audit_root, rows)
    for i, row in enumerate(rows):
        fresh_ts.label_decision_id(
            f"id{i}",
            {k: True for k in fresh_ts._LABELS},
        )
    assert fresh_ts.should_prompt_for_label() is False


def test_cli_label_with_labels_flag(fresh_ts, tmp_path, monkeypatch):
    audit_root = tmp_path / "audit"
    _write_audit(audit_root, [{
        "decision_id": "abc123",
        "session_id": "session",
        "file_path": "src/foo.py",
        "decision": "blocked",
        "signal_source": "plan_grounding",
    }])
    monkeypatch.setenv("RC_TRAINING_SET_FILE", str(tmp_path / "training_set.jsonl"))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_root))

    sys.path.insert(0, str(REPO_ROOT / "src"))
    import rc_cli
    importlib.reload(rc_cli)

    rc = rc_cli.main(["label", "abc123",
                       "--labels", "scope_drift=yes,plan_violation=yes",
                       "--yes"])
    assert rc == 0
    assert fresh_ts.already_labeled("abc123") is True


def test_cli_label_random(fresh_ts, tmp_path, monkeypatch):
    audit_root = tmp_path / "audit"
    _write_audit(audit_root, [
        {"decision_id": "id1", "session_id": "s", "file_path": "f.py",
         "decision": "allowed", "signal_source": "ssm"},
    ])
    monkeypatch.setenv("RC_TRAINING_SET_FILE", str(tmp_path / "training_set.jsonl"))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_root))

    sys.path.insert(0, str(REPO_ROOT / "src"))
    import rc_cli
    importlib.reload(rc_cli)

    rc = rc_cli.main(["label", "--random", "--labels", "scope_drift=no",
                       "--yes"])
    assert rc == 0
    assert fresh_ts.already_labeled("id1") is True


def test_cli_label_stats(fresh_ts, tmp_path, monkeypatch):
    audit_root = tmp_path / "audit"
    rows = [
        {"decision_id": f"id{i}", "session_id": "s", "file_path": f"f{i}.py",
         "decision": "allowed"}
        for i in range(50)
    ]
    _write_audit(audit_root, rows)
    for i in range(50):
        fresh_ts.label_decision_id(
            f"id{i}",
            {label: True for label in fresh_ts._LABELS},
        )

    monkeypatch.setenv("RC_TRAINING_SET_FILE", str(tmp_path / "training_set.jsonl"))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_root))

    sys.path.insert(0, str(REPO_ROOT / "src"))
    import rc_cli
    importlib.reload(rc_cli)

    rc = rc_cli.main(["label-stats"])
    assert rc == 0  # all targets met
    assert fresh_ts.count_per_label()["scope_drift"] == 50


def test_cli_label_stats_via_subprocess(tmp_path):
    audit_root = tmp_path / "audit"
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    day_dir = audit_root / day
    day_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"decision_id": f"id{i}", "session_id": "s", "file_path": f"f{i}.py",
         "decision": "allowed"}
        for i in range(60)
    ]
    (day_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8",
    )
    training_set = tmp_path / "training_set.jsonl"
    label_rows = []
    for i in range(60):
        # Each label gets exactly 12 positives (above target of 10)
        labels = {k: False for k in ("scope_drift", "plan_violation",
                                       "structural_regression",
                                       "syntax_type_error", "test_failure")}
        for j, k in enumerate(("scope_drift", "plan_violation",
                              "structural_regression",
                              "syntax_type_error", "test_failure")):
            if i % 5 == j:
                labels[k] = True
        label_rows.append({
            "decision_id": f"id{i}", "session_id": "s",
            "file_path": f"f{i}.py", "decision": "allowed",
            "signal_source": "",
            "labels": labels, "notes": "", "labeler_id": "test",
            "ts": "2026-07-16T00:00:00Z",
            "rationale_quality_failure": True,
        })
    training_set.write_text(
        "\n".join(json.dumps(r) for r in label_rows) + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["RC_TRAINING_SET_FILE"] = str(training_set)
    env["RC_AUDIT_ROOT"] = str(audit_root)
    rc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "src" / "rc_cli.py"), "label-stats"],
        capture_output=True, text=True, env=env, timeout=15,
    )
    assert rc.returncode == 0, rc.stderr
    assert "ALL LABELS REACHED TARGET" in rc.stdout