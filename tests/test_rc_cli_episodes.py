"""Tests for `rc episodes` and file-scoped verification receipts."""
from __future__ import annotations

import datetime as _dt
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture
def isolated_rc(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit_dir = tmp_path / "events"
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit_dir))
    monkeypatch.setenv("RC_SESSION_ID", "rc-episodes-test")
    import audit_log as al
    import rc_cli
    importlib.reload(al)
    importlib.reload(rc_cli)
    return tmp_path, rc_cli, al


def _write_events(root: Path, session_id: str, events: list[dict]) -> None:
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    target = root / day
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def _sample_events() -> list[dict]:
    return [
        {
            "event_type": "decision",
            "decision_id": "e1",
            "session_id": "s1",
            "project_dir": "/repo",
            "ts": "2026-09-10T10:00:00+00:00",
            "file_path": "/repo/foo.py",
            "file_path_rel": "foo.py",
            "decision": "allowed",
            "tool_name": "Edit",
        },
        {
            "event_type": "verification_recorded",
            "decision_id": "c1",
            "session_id": "s1",
            "project_dir": "/repo",
            "ts": "2026-09-10T10:00:01+00:00",
            "verification_kind": "parse",
            "verification_status": "failed",
            "parent_decision_id": "e1",
            "deterministic": True,
        },
        {
            "event_type": "decision",
            "decision_id": "e2",
            "session_id": "s1",
            "project_dir": "/repo",
            "ts": "2026-09-10T10:00:02+00:00",
            "file_path": "/repo/foo.py",
            "file_path_rel": "foo.py",
            "decision": "allowed",
            "tool_name": "Edit",
        },
        {
            "event_type": "verification_recorded",
            "decision_id": "c2",
            "session_id": "s1",
            "project_dir": "/repo",
            "ts": "2026-09-10T10:00:03+00:00",
            "verification_kind": "parse",
            "verification_status": "passed",
            "parent_decision_id": "e2",
            "deterministic": True,
        },
    ]


def test_episodes_cli_emits_episode_json(tmp_path, capsys):
    import rc_cli

    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", _sample_events())

    rc = rc_cli.main([
        "episodes", "--audit-root", str(audit_root), "--days", "1",
        "--include-synthetic", "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    episode = payload["episodes"][0]
    assert episode["episode_id"] == "s1:e1"
    assert episode["final_status"] == "verified_passed"
    assert episode["checks"][0]["exact"] is True


def test_episodes_cli_renders_markdown(tmp_path, capsys):
    import rc_cli

    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", _sample_events())

    rc = rc_cli.main([
        "episodes", "--audit-root", str(audit_root), "--days", "1",
        "--include-synthetic",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "# reasoning-core edit episodes" in out
    assert "verified_passed: 1" in out


def test_episodes_cli_session_filter(tmp_path, capsys):
    import rc_cli

    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", _sample_events())
    _write_events(audit_root, "s2", [{**_sample_events()[0], "session_id": "s2"}])

    rc = rc_cli.main([
        "episodes", "--audit-root", str(audit_root), "--days", "1",
        "--session-id", "s2", "--include-synthetic", "--json",
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [episode["session_id"] for episode in payload["episodes"]] == ["s2"]


def test_record_verification_persists_file_path(isolated_rc):
    tmp_path, rc_cli, _al = isolated_rc
    project = tmp_path / "project"
    project.mkdir()
    target = project / "foo.py"
    target.write_text("value = 1\n", encoding="utf-8")

    rc = rc_cli.main([
        "record-verification",
        "--kind", "parse",
        "--status", "failed",
        "--project-dir", str(project),
        "--session-id", "rc-episodes-test",
        "--file-path", str(target),
    ])

    assert rc == 0
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    events = [
        json.loads(line)
        for line in (tmp_path / "events" / day / "rc-episodes-test.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert events[-1]["file_path_rel"] == "foo.py"
