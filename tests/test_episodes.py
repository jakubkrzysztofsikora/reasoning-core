"""Tests for the canonical agent edit-episode builder and recovery policy."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.hooks import _episodes as ep


def _write_events(root: Path, session_id: str, events: list[dict]) -> None:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    target = root / day
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def _edit(decision_id: str, ts: str, *, session: str = "s1", repo: str = "/repo", file: str = "foo.py") -> dict:
    return {
        "event_type": "decision",
        "decision_id": decision_id,
        "session_id": session,
        "project_dir": repo,
        "ts": ts,
        "file_path": f"{repo}/{file}",
        "file_path_rel": file,
        "decision": "allowed",
        "tool_name": "Edit",
        "signal_source": "ssm",
    }


def _check(
    decision_id: str,
    ts: str,
    status: str,
    *,
    parent: str | None = None,
    session: str = "s1",
    repo: str = "/repo",
    file: str | None = "foo.py",
) -> dict:
    event = {
        "event_type": "verification_recorded",
        "decision_id": decision_id,
        "session_id": session,
        "project_dir": repo,
        "ts": ts,
        "verification_kind": "parse",
        "verification_status": status,
        "deterministic": True,
        "association_type": "explicit_decision" if parent else "session_level",
    }
    if parent:
        event["parent_decision_id"] = parent
    if file:
        event["file_path"] = f"{repo}/{file}"
        event["file_path_rel"] = file
    return event


def test_explicit_chain_groups_edits_checks_and_repair(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c1", "2026-09-10T10:00:01+00:00", "failed", parent="e1"),
        _edit("e2", "2026-09-10T10:00:02+00:00"),
        _check("c2", "2026-09-10T10:00:03+00:00", "passed", parent="e2"),
        {
            "event_type": "session_outcome_recorded",
            "decision_id": "o1",
            "session_id": "s1",
            "project_dir": "/repo",
            "ts": "2026-09-10T10:01:00+00:00",
            "outcome_status": "verified_clean",
            "git_head_after": "abc123",
        },
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episodes = ep.build_episodes(audit_root, days=1, include_synthetic=True)

    assert len(episodes) == 1
    episode = episodes[0]
    assert episode["episode_id"] == "s1:e1"
    assert episode["root_decision_id"] == "e1"
    assert episode["session_id"] == "s1"
    assert [event["decision_id"] for event in episode["edit_events"]] == ["e1", "e2"]
    assert [check["decision_id"] for check in episode["checks"]] == ["c1", "c2"]
    assert all(check["association"] == "explicit_decision" for check in episode["checks"])
    assert all(check["exact"] is True for check in episode["checks"])
    assert len(episode["repair_attempts"]) == 1
    assert episode["repair_attempts"][0]["decision_id"] == "e2"
    assert episode["repair_attempts"][0]["after_check_id"] == "c1"
    assert episode["final_status"] == "verified_passed"
    assert episode["session_outcome"]["outcome_status"] == "verified_clean"


def test_session_level_check_is_attached_but_never_exact(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c1", "2026-09-10T10:00:05+00:00", "failed", file="foo.py"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert episode["checks"][0]["association"] == "session_level"
    assert episode["checks"][0]["attachment"] == "latest_preceding_edit"
    assert episode["checks"][0]["exact"] is False
    assert episode["final_status"] == "failed_pending_repair"


def test_identical_timestamps_break_ties_deterministically(tmp_path):
    same_ts = "2026-09-10T10:00:05+00:00"
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c-pass", same_ts, "passed", parent="e1"),
        _check("c-fail", same_ts, "failed", parent="e1"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert episode["final_status"] == "failed_pending_repair"


def test_checks_without_file_or_parent_are_not_guessed_into_an_episode(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("unknown", "2026-09-10T10:00:05+00:00", "failed", file=None),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert episode["checks"] == []
    assert episode["final_status"] == "pending"


def test_episodes_split_by_task_id(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        {**_edit("e2", "2026-09-10T10:00:01+00:00"), "task_id": "t2"},
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episodes = ep.build_episodes(audit_root, days=1, include_synthetic=True)

    assert [episode["root_decision_id"] for episode in episodes] == ["e1", "e2"]


def test_synthetic_canary_fixtures_are_excluded_by_default(tmp_path):
    events = [
        {**_edit("e1", "2026-09-10T10:00:00+00:00"), "project_dir": "/tmp/rc-doctor-canary-abc"},
        {
            **_edit("e2", "2026-09-10T10:00:01+00:00"),
            "session_id": "rc-doctor-canary-abc",
        },
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    assert ep.build_episodes(audit_root, days=1) == []
    assert len(ep.build_episodes(audit_root, days=1, include_synthetic=True)) == 2


@pytest.mark.parametrize(
    ("failures", "status", "attempt"),
    [(0, "ok", 0), (1, "repair", 1), (2, "repair", 2), (3, "abstain", 2)],
)
def test_recovery_policy_bounds_repairs_at_two(failures, status, attempt):
    policy = ep.recovery_policy(failures)

    assert policy["status"] == status
    assert policy["attempt"] == attempt
    assert policy["max_attempts"] == ep.MAX_REPAIR_ATTEMPTS
    if status == "abstain":
        assert "ask the user" in policy["next_action"]
    elif status == "repair":
        assert "rerun" in policy["next_action"]


def test_repair_state_uses_failed_checks_from_episode(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c1", "2026-09-10T10:00:01+00:00", "failed", parent="e1"),
        _edit("e2", "2026-09-10T10:00:02+00:00"),
        _check("c2", "2026-09-10T10:00:03+00:00", "failed", parent="e2"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]
    state = ep.repair_state(episode)

    assert state["status"] == "repair"
    assert state["attempt"] == 2
    assert episode["final_status"] == "failed_pending_repair"


def test_failed_checks_in_same_batch_count_as_one_attempt(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        {**_check("c1a", "2026-09-10T10:00:01+00:00", "failed", parent="e1"), "check_batch_id": "batch-1"},
        {**_check("c1b", "2026-09-10T10:00:01+00:00", "failed", parent="e1"), "check_batch_id": "batch-1", "verification_kind": "lint"},
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]
    state = ep.repair_state(episode)

    assert state["status"] == "repair"
    assert state["attempt"] == 1


def test_session_level_check_attaches_to_latest_preceding_task_episode(tmp_path):
    events = [
        {**_edit("e1", "2026-09-10T10:00:00+00:00"), "task_id": "t1"},
        {**_edit("e2", "2026-09-10T10:00:10+00:00"), "task_id": "t2"},
        _check("c1", "2026-09-10T10:00:20+00:00", "failed", file="foo.py"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episodes = ep.build_episodes(audit_root, days=1, include_synthetic=True)

    by_root = {episode["root_decision_id"]: episode for episode in episodes}
    assert by_root["e1"]["checks"] == []
    assert [check["decision_id"] for check in by_root["e2"]["checks"]] == ["c1"]


def test_checks_with_unparseable_timestamps_are_dropped(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        {**_check("c-bad", "", "failed", parent="e1")},
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert episode["checks"] == []
    assert episode["final_status"] == "pending"


def test_mixed_timestamp_formats_use_parsed_time_for_latest_check(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c-old", "2026-09-10T10:30:00Z", "failed", parent="e1"),
        _check("c-new", "2026-09-10T10:31:00+00:00", "passed", parent="e1"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert episode["final_status"] == "verified_passed"


def test_repair_state_abstains_after_third_failed_check(tmp_path):
    events = [
        _edit("e1", "2026-09-10T10:00:00+00:00"),
        _check("c1", "2026-09-10T10:00:01+00:00", "failed", parent="e1"),
        _edit("e2", "2026-09-10T10:00:02+00:00"),
        _check("c2", "2026-09-10T10:00:03+00:00", "failed", parent="e2"),
        _edit("e3", "2026-09-10T10:00:04+00:00"),
        _check("c3", "2026-09-10T10:00:05+00:00", "failed", parent="e3"),
    ]
    audit_root = tmp_path / "events"
    _write_events(audit_root, "s1", events)

    episode = ep.build_episodes(audit_root, days=1, include_synthetic=True)[0]

    assert ep.repair_state(episode)["status"] == "abstain"
    assert episode["final_status"] == "failed_budget_exhausted"
