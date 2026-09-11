"""Join real-session reasoning decisions with labels, checks, and commits.

The correlator is deliberately local-first and conservative. Exact links use
decision IDs, session/project IDs, explicit Git head ranges, and deterministic
verification receipts. Ambiguous temporal matches are reported as gaps rather
than promoted to causal evidence.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


_DECISIONS = frozenset({
    "allowed",
    "allowed_via_override",
    "blocked",
    "shadow_blocked",
    "warn",
    "fail-open",
    "override_declined",
})
_OUTCOME_EVENT = "session_outcome_recorded"
_VERIFICATION_EVENT = "verification_recorded"
_START_EVENT = "session_started"


def _has_canary_component(path: str) -> bool:
    """Match canary dirs by path component, not mid-name substrings."""
    if not path:
        return False
    try:
        return any(
            part.startswith("rc-doctor-canary-") for part in Path(path).parts
        )
    except (OSError, ValueError):
        return False


def _is_synthetic_fixture(event: dict[str, Any]) -> bool:
    """Recognize test/fixture paths that must not enter product evidence."""
    project = str(event.get("project_dir") or "")
    session = str(event.get("session_id") or event.get("source_session_id") or "")
    return (
        project == "/fake/repo"
        or project.startswith("/fake/")
        or "/pytest-of-" in project
        or "/pytest-" in project
        or _has_canary_component(project)
        or session.startswith("rc-doctor-canary-")
    )


def _parse_ts(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _session_key(event: dict[str, Any]) -> tuple[str, str]:
    project = str(event.get("project_dir") or "")
    try:
        project = str(Path(project).expanduser().resolve()) if project else ""
    except OSError:
        pass
    return project, str(event.get("session_id") or event.get("source_session_id") or "")


def _iter_day_dirs(root: Path, days: int) -> Iterable[Path]:
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for offset in range(max(1, days)):
        day_dir = root / (today - _dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        if day_dir.is_dir():
            yield day_dir


def load_audit_events(audit_root: Path, *, days: int = 30) -> list[dict[str, Any]]:
    """Load recent audit rows while tolerating malformed/rotated files."""
    events: list[dict[str, Any]] = []
    for day_dir in _iter_day_dirs(audit_root, days):
        paths = sorted(day_dir.glob("*.jsonl")) + sorted(day_dir.glob("*.jsonl.gz"))
        for path in paths:
            try:
                opener = gzip.open if path.suffix == ".gz" else open
                with opener(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(row, dict):
                            events.append(row)
            except OSError:
                continue
    return events


def load_labels(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load labels keyed by exact decision_id."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not path.is_file():
        return out
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(row, dict) or not row.get("decision_id"):
                    continue
                out.setdefault(str(row["decision_id"]), []).append(row)
    except OSError:
        pass
    return out


def _is_decision(event: dict[str, Any]) -> bool:
    if event.get("event_type") in {_OUTCOME_EVENT, _VERIFICATION_EVENT, _START_EVENT}:
        return False
    return bool(event.get("file_path")) and (
        event.get("decision") in _DECISIONS or event.get("tool_name") in {"Edit", "Write", "MultiEdit"}
    )


def _same_context(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = _session_key(left)
    right_key = _session_key(right)
    if left_key != right_key:
        return False
    for key in ("run_id", "task_id"):
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value and right_value:
            return left_value == right_value
    return True


def _git_commits_between(project_dir: str, before: str, after: str) -> list[dict[str, Any]]:
    if not project_dir or not before or not after or before == after:
        return []
    try:
        revs = subprocess.run(
            ["git", "rev-list", "--ancestry-path", f"{before}..{after}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if revs.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []

    commits: list[dict[str, Any]] = []
    for sha in [line.strip() for line in revs.stdout.splitlines() if line.strip()]:
        try:
            meta = subprocess.run(
                ["git", "show", "-s", "--format=%H%x00%ct%x00%s", sha],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            files = subprocess.run(
                ["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if meta.returncode != 0 or files.returncode != 0:
            continue
        parts = meta.stdout.strip().split("\x00", 2)
        if len(parts) != 3:
            continue
        try:
            timestamp = _dt.datetime.fromtimestamp(int(parts[1]), tz=_dt.timezone.utc).isoformat()
        except ValueError:
            timestamp = ""
        commits.append({
            "sha": parts[0],
            "ts": timestamp,
            "message": parts[2],
            "files": [line.strip() for line in files.stdout.splitlines() if line.strip()],
        })
    return commits


def _session_outcome(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    outcomes = [event for event in events if event.get("event_type") == _OUTCOME_EVENT]
    if not outcomes:
        return None
    return sorted(outcomes, key=lambda event: event.get("ts", ""))[-1]


def build_ledger(
    audit_root: Path,
    *,
    training_set: Path | None = None,
    days: int = 30,
    project_dir: str | None = None,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    """Build a conservative evidence ledger from recent real sessions."""
    raw_events = load_audit_events(audit_root, days=days)
    synthetic_events = 0
    events = raw_events
    if not include_synthetic:
        events = [event for event in raw_events if not _is_synthetic_fixture(event)]
        synthetic_events = len(raw_events) - len(events)
    labels = load_labels(
        training_set
        or Path(os.environ.get(
            "RC_TRAINING_SET_FILE",
            os.path.expanduser("~/.local/share/reasoning-core/training_set.jsonl"),
        ))
    )
    project_filter = str(Path(project_dir).expanduser().resolve()) if project_dir else None
    if project_filter:
        events = [event for event in events if _session_key(event)[0] == project_filter]

    by_session: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        by_session.setdefault(_session_key(event), []).append(event)

    decisions: list[dict[str, Any]] = []
    linked_commits: set[str] = set()
    for event in events:
        if not _is_decision(event):
            continue
        key = _session_key(event)
        session_events = by_session[key]
        exact_verifications = [
            candidate for candidate in session_events
            if candidate.get("event_type") == _VERIFICATION_EVENT
            and candidate.get("parent_decision_id") == event.get("decision_id")
        ]
        outcome = _session_outcome(session_events)
        commits: list[dict[str, Any]] = []
        if outcome and event.get("git_head_before") and outcome.get("git_head_after"):
            for commit in _git_commits_between(
                key[0],
                str(event["git_head_before"]),
                str(outcome["git_head_after"]),
            ):
                if event.get("file_path_rel") in commit.get("files", []):
                    linked = {**commit, "link_confidence": "exact_head_range"}
                    commits.append(linked)
                    linked_commits.add(commit["sha"])

        evidence_parts = []
        if event.get("decision_id") in labels:
            evidence_parts.append("label")
        if exact_verifications:
            evidence_parts.append("verification")
        if outcome:
            evidence_parts.append("session_outcome")
        if commits:
            evidence_parts.append("commit")
        evidence_grade = (
            "strong" if any(part in evidence_parts for part in ("verification", "commit"))
            else "partial" if evidence_parts
            else "unlinked"
        )
        decisions.append({
            "decision_id": event.get("decision_id"),
            "session_id": event.get("session_id"),
            "project_dir": key[0],
            "ts": event.get("ts"),
            "file_path": event.get("file_path"),
            "file_path_rel": event.get("file_path_rel"),
            "decision": event.get("decision"),
            "signal_source": event.get("signal_source"),
            "run_id": event.get("run_id"),
            "task_id": event.get("task_id"),
            "transcript_path": event.get("transcript_path"),
            "tool_call_id": event.get("tool_call_id"),
            "turn_index": event.get("turn_index"),
            "labels": labels.get(event.get("decision_id"), []),
            "exact_verifications": exact_verifications,
            "session_outcome": outcome,
            "commits": commits,
            "evidence_parts": evidence_parts,
            "evidence_grade": evidence_grade,
        })

    sessions: list[dict[str, Any]] = []
    for key, session_events in sorted(by_session.items()):
        verifications = [event for event in session_events if event.get("event_type") == _VERIFICATION_EVENT]
        outcomes = [event for event in session_events if event.get("event_type") == _OUTCOME_EVENT]
        sessions.append({
            "project_dir": key[0],
            "session_id": key[1],
            "decision_count": sum(1 for event in session_events if _is_decision(event)),
            "verification_count": len(verifications),
            "outcome_count": len(outcomes),
            "outcomes": outcomes,
        })

    exact_links = sum(1 for decision in decisions if decision["exact_verifications"])
    failed_links = sum(
        1 for decision in decisions
        if decision["decision"] in {"allowed", "warn", "allowed_via_override"}
        and any(v.get("verification_status") == "failed" for v in decision["exact_verifications"])
    )
    blocked_pass_links = sum(
        1 for decision in decisions
        if decision["decision"] in {"blocked", "shadow_blocked"}
        and any(v.get("verification_status") == "passed" for v in decision["exact_verifications"])
    )
    outcome_counts = {
        status: sum(
            1 for session in sessions
            for outcome in session["outcomes"]
            if outcome.get("outcome_status") == status
        )
        for status in ("verified_clean", "unverified_gap", "unverified_infrastructure")
    }
    sessions_with_outcome = sum(1 for session in sessions if session["outcomes"])
    labeled_decisions = sum(1 for decision in decisions if decision["labels"])
    linked_commit_decisions = sum(1 for decision in decisions if decision["commits"])

    def _rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 6) if denominator else None

    summary = {
        "raw_events_loaded": len(raw_events),
        "events_loaded": len(events),
        "synthetic_events_excluded": synthetic_events,
        "sessions": len(sessions),
        "decisions": len(decisions),
        "labeled_decisions": labeled_decisions,
        "exact_verification_links": exact_links,
        "linked_commit_decisions": linked_commit_decisions,
        "linked_commits": len(linked_commits),
        "allowed_or_warn_with_failed_verification": failed_links,
        "blocked_with_passed_verification": blocked_pass_links,
        "outcomes": outcome_counts,
        "coverage": {
            "label_rate": _rate(labeled_decisions, len(decisions)),
            "exact_verification_rate": _rate(exact_links, len(decisions)),
            "session_outcome_rate": _rate(sessions_with_outcome, len(sessions)),
            "commit_link_rate": _rate(linked_commit_decisions, len(decisions)),
        },
    }
    recommendations: list[str] = []
    if decisions and exact_links == 0:
        recommendations.append("Pass parent_decision_id when recording checks; no exact verification joins exist.")
    if summary["sessions"] and sum(outcome_counts.values()) < summary["sessions"]:
        recommendations.append("Increase Stop-hook coverage; some sessions have no persisted outcome.")
    if failed_links:
        recommendations.append("Review allowed decisions followed by deterministic failures before changing thresholds.")
    if blocked_pass_links:
        recommendations.append("Review blocked decisions followed by passing checks as false-block candidates; do not auto-promote neural evidence.")
    if not recommendations:
        recommendations.append("Keep defaults unchanged until the next rolling window confirms deterministic outcome improvements.")

    return {
        "schema_version": 1,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "window_days": days,
        "summary": summary,
        "recommendations": recommendations,
        "sessions": sessions,
        "decisions": decisions,
    }


def render_markdown(ledger: dict[str, Any]) -> str:
    summary = ledger.get("summary", {})
    lines = [
        "# Real-session reasoning-core evaluation",
        "",
        "> Advisory evidence ledger; deterministic verification links are stronger than inferred correlations.",
        "",
        f"- window: last {ledger.get('window_days', '?')} days",
        f"- synthetic fixture events excluded: {summary.get('synthetic_events_excluded', 0)}",
        f"- sessions: {summary.get('sessions', 0)}",
        f"- decisions: {summary.get('decisions', 0)}",
        f"- labeled decisions: {summary.get('labeled_decisions', 0)}",
        f"- exact verification links: {summary.get('exact_verification_links', 0)}",
        f"- linked commit decisions: {summary.get('linked_commit_decisions', 0)}",
        f"- coverage: {json.dumps(summary.get('coverage', {}), sort_keys=True)}",
        "",
        "## Deterministic signals",
        "",
        f"- allowed/warn decisions followed by failed verification: {summary.get('allowed_or_warn_with_failed_verification', 0)}",
        f"- blocked decisions followed by passed verification: {summary.get('blocked_with_passed_verification', 0)}",
        f"- outcomes: {json.dumps(summary.get('outcomes', {}), sort_keys=True)}",
        "",
        "## Next actions",
        "",
    ]
    lines.extend(f"- {item}" for item in ledger.get("recommendations", []))
    return "\n".join(lines) + "\n"


__all__ = ["build_ledger", "load_audit_events", "load_labels", "render_markdown"]
