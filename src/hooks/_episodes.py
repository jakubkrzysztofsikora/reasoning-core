"""Canonical local episode ledger: edit -> checks -> repair -> outcome.

Episodes are derived from the existing per-session audit JSONL. The builder is
deliberately conservative: exact links require an explicit ``parent_decision_id``
from the host and are labelled ``explicit_parent``. Session-level checks are
attached to the latest preceding edit on the same file, labelled
``latest_preceding_edit`` so consumers can filter them; exact links never use
recency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.hooks import _session_correlator as _sc

SCHEMA_VERSION = 1
MAX_REPAIR_ATTEMPTS = 2

_VERIFICATION_EVENT = "verification_recorded"
_OUTCOME_EVENT = "session_outcome_recorded"


def _ts(event: dict[str, Any]) -> str:
    return str(event.get("ts") or "")


def _ts_key(value: str) -> tuple[float, str]:
    """Sort key that parses both ``Z`` and ``+00:00`` forms correctly."""
    parsed = _sc._parse_ts(value)
    if parsed is None:
        return (float("-inf"), str(value))
    try:
        return (parsed.timestamp(), str(value))
    except (OSError, OverflowError, ValueError):
        return (float("-inf"), str(value))


def _file_identity(event: dict[str, Any]) -> str:
    return str(event.get("file_path_rel") or event.get("file_path") or "")


def recovery_policy(failure_count: int) -> dict[str, Any]:
    """Bounded recovery policy: max 2 repair attempts, then abstain."""
    count = max(0, int(failure_count))
    if count == 0:
        return {
            "status": "ok",
            "attempt": 0,
            "max_attempts": MAX_REPAIR_ATTEMPTS,
            "next_action": "no repair needed",
        }
    if count > MAX_REPAIR_ATTEMPTS:
        return {
            "status": "abstain",
            "attempt": MAX_REPAIR_ATTEMPTS,
            "max_attempts": MAX_REPAIR_ATTEMPTS,
            "next_action": "stop and ask the user; do not attempt further repairs",
        }
    return {
        "status": "repair",
        "attempt": count,
        "max_attempts": MAX_REPAIR_ATTEMPTS,
        "next_action": "repair, then rerun the failing check",
    }


def failed_attempts(checks: list[dict[str, Any]]) -> int:
    """Count distinct failed check invocations, not failed check rows.

    One invocation can fail several kinds (parse + lint); the repair budget is
    about attempts, so rows sharing a ``check_batch_id`` count once.
    """
    batches: set[str] = set()
    for check in checks:
        if check.get("verification_status") != "failed":
            continue
        batches.add(str(check.get("check_batch_id") or check.get("decision_id") or ""))
    return len(batches)


def repair_state(episode: dict[str, Any]) -> dict[str, Any]:
    """Recovery state for an episode, derived from its failed check batches."""
    return recovery_policy(failed_attempts(episode.get("checks", [])))


def _check_entry(check: dict[str, Any], association: str, exact: bool) -> dict[str, Any]:
    return {
        "decision_id": check.get("decision_id"),
        "ts": _ts(check),
        "verification_kind": check.get("verification_kind"),
        "verification_status": check.get("verification_status"),
        "association": association,
        "attachment": (
            "explicit_parent" if exact else "latest_preceding_edit"
        ),
        "exact": exact,
        "command": check.get("command") or "",
        "exit_code": check.get("exit_code"),
        "check_batch_id": check.get("check_batch_id"),
    }


def _final_status(checks: list[dict[str, Any]], recovery: dict[str, Any]) -> str:
    if not checks:
        return "pending"
    # Failed wins timestamp ties: pessimistic and reproducible.
    last = max(
        checks,
        key=lambda check: (
            _ts_key(check["ts"]),
            1 if check["verification_status"] == "failed" else 0,
            str(check.get("decision_id") or ""),
        ),
    )
    if last["verification_status"] == "passed":
        return "verified_passed"
    if recovery["status"] == "abstain":
        return "failed_budget_exhausted"
    return "failed_pending_repair"


def _session_outcome(outcome: dict[str, Any] | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    return {
        key: outcome.get(key)
        for key in (
            "decision_id", "ts", "outcome_status", "reconcile_status",
            "missing_count", "git_head_after",
        )
        if outcome.get(key) is not None
    }


def build_episodes(
    audit_root: Path,
    *,
    days: int = 30,
    session_id: str | None = None,
    project_dir: str | None = None,
    include_synthetic: bool = False,
) -> list[dict[str, Any]]:
    """Build edit episodes from recent audit rows."""
    raw_events = _sc.load_audit_events(Path(audit_root), days=days)
    events = raw_events if include_synthetic else [
        event for event in raw_events if not _sc._is_synthetic_fixture(event)
    ]
    if project_dir:
        project_filter = str(Path(project_dir).expanduser().resolve())
        events = [event for event in events if _sc._session_key(event)[0] == project_filter]
    if session_id:
        events = [event for event in events if _sc._session_key(event)[1] == session_id]

    by_session: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        by_session.setdefault(_sc._session_key(event), []).append(event)

    episodes: list[dict[str, Any]] = []
    for (project, sid), session_events in by_session.items():
        edits = [
            event for event in session_events
            if _sc._is_decision(event) and _sc._parse_ts(_ts(event)) is not None
        ]
        checks = [
            event for event in session_events
            if event.get("event_type") == _VERIFICATION_EVENT
            and _sc._parse_ts(_ts(event)) is not None
        ]
        outcomes = [
            event for event in session_events
            if event.get("event_type") == _OUTCOME_EVENT
        ]
        outcome = max(outcomes, key=lambda item: _ts_key(_ts(item))) if outcomes else None

        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for edit in edits:
            groups.setdefault(
                (str(edit.get("task_id") or ""), _file_identity(edit)), []
            ).append(edit)
        for group in groups.values():
            group.sort(key=lambda edit: _ts_key(_ts(edit)))

        # Attach session-level checks to the latest preceding edit group on the
        # same file, even when the file was edited under several task_ids.
        session_level_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {
            group_key: [] for group_key in groups
        }
        latest_by_group: dict[tuple[str, str], tuple[float, str]] = {
            group_key: max(
                (_ts_key(_ts(edit)) for edit in group),
                default=(float("-inf"), ""),
            )
            for group_key, group in groups.items()
        }
        for check in checks:
            if check.get("parent_decision_id"):
                continue
            identity = _file_identity(check)
            if not identity:
                continue
            check_key = _ts_key(_ts(check))
            best_key: tuple[str, str] | None = None
            best_latest: tuple[float, str] | None = None
            for group_key, latest in latest_by_group.items():
                if group_key[1] != identity:
                    continue
                if latest[0] == float("-inf") or latest > check_key:
                    continue
                if best_latest is None or latest > best_latest:
                    best_key, best_latest = group_key, latest
            if best_key is not None:
                session_level_by_group[best_key].append(check)

        for (_task_id, identity), edit_group in groups.items():
            root = edit_group[0]
            decision_ids = {edit.get("decision_id") for edit in edit_group}

            explicit = [
                check for check in checks
                if check.get("parent_decision_id") in decision_ids
            ]
            session_level = session_level_by_group[(_task_id, identity)]

            check_entries = [
                _check_entry(check, "explicit_decision", True)
                for check in sorted(explicit, key=lambda item: _ts_key(_ts(item)))
            ] + [
                _check_entry(check, "session_level", False)
                for check in sorted(session_level, key=lambda item: _ts_key(_ts(item)))
            ]

            repair_attempts: list[dict[str, Any]] = []
            for edit in edit_group[1:]:
                edit_key = _ts_key(_ts(edit))
                prior_failed = [
                    check for check in check_entries
                    if check["verification_status"] == "failed"
                    and _ts_key(check["ts"]) < edit_key
                ]
                if prior_failed:
                    last_failed = max(prior_failed, key=lambda check: _ts_key(check["ts"]))
                    repair_attempts.append({
                        "decision_id": edit.get("decision_id"),
                        "ts": _ts(edit),
                        "after_check_id": last_failed["decision_id"],
                    })

            recovery = recovery_policy(failed_attempts(check_entries))
            all_ts = [_ts(event) for event in edit_group] + [
                check["ts"] for check in check_entries
            ]
            if outcome:
                all_ts.append(_ts(outcome))
            episodes.append({
                "schema_version": SCHEMA_VERSION,
                "episode_id": f"{sid}:{root.get('decision_id')}",
                "session_id": sid,
                "task_id": root.get("task_id"),
                "project_dir": project,
                "root_decision_id": root.get("decision_id"),
                "file_path": root.get("file_path"),
                "file_path_rel": identity or None,
                "started_ts": min(all_ts, key=_ts_key) if all_ts else "",
                "ended_ts": max(all_ts, key=_ts_key) if all_ts else "",
                "edit_events": [
                    {
                        "decision_id": edit.get("decision_id"),
                        "ts": _ts(edit),
                        "decision": edit.get("decision"),
                        "tool_name": edit.get("tool_name"),
                    }
                    for edit in edit_group
                ],
                "checks": check_entries,
                "repair_attempts": repair_attempts,
                "final_status": _final_status(check_entries, recovery),
                "session_outcome": _session_outcome(outcome),
            })

    episodes.sort(
        key=lambda episode: (_ts_key(episode["started_ts"]), episode["episode_id"])
    )
    return episodes


def render_markdown(episodes: list[dict[str, Any]]) -> str:
    lines = [
        "# reasoning-core edit episodes",
        "",
        "> Derived from local audit rows; session-level checks are never promoted to exact links.",
        "",
        f"- episodes: {len(episodes)}",
    ]
    counts: dict[str, int] = {}
    for episode in episodes:
        counts[episode["final_status"]] = counts.get(episode["final_status"], 0) + 1
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.append("")
    for episode in episodes:
        checks = episode.get("checks", [])
        exact = sum(1 for check in checks if check["exact"])
        lines.append(
            f"- {episode['episode_id']} [{episode['final_status']}] "
            f"{episode.get('file_path_rel') or episode.get('file_path') or '?'} "
            f"edits={len(episode.get('edit_events', []))} checks={len(checks)} "
            f"exact={exact} repairs={len(episode.get('repair_attempts', []))}"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "SCHEMA_VERSION",
    "build_episodes",
    "recovery_policy",
    "render_markdown",
    "repair_state",
]
