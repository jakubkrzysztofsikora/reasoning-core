"""Commit miner for Phase 4 self-improving calibration.

Mines the repo's git history, labels commits as positive/negative using a
defined heuristic, and extracts features that the calibration engine can use
to recalibrate thresholds.

Heuristic:
  - Positive: commit is at HEAD and not followed within 48 hours by a commit
    touching the same files where the later message matches fix|revert|hotfix|patch
    or reverts >30% of the earlier commit's changed lines.
  - Negative: followed within 48 hours by such a commit.

This is intentionally conservative: it may miss some bad commits, but it
rarely mislabels good ones, which is the right bias for threshold training.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_LABEL_RE = re.compile(r"\b(fix|revert|hotfix|patch)\b", re.IGNORECASE)


@dataclass
class MinedCommit:
    sha: str = ""
    author: str = ""
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""
    files: List[str] = field(default_factory=list)
    diff_stat: Dict[str, int] = field(default_factory=dict)
    label: Optional[str] = None  # "positive" | "negative" | None
    label_reason: str = ""
    features: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sha": self.sha,
            "author": self.author,
            "date": self.date.isoformat(),
            "message": self.message,
            "files": self.files,
            "diff_stat": self.diff_stat,
            "label": self.label,
            "label_reason": self.label_reason,
            "features": self.features,
        }


def _run_git(args: List[str], cwd: str, timeout: float = 10.0) -> str:
    """Run a git command and return stdout; raise on failure."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def _is_git_repo(project_root: str) -> bool:
    try:
        _run_git(["rev-parse", "--is-inside-work-tree"], project_root)
        return True
    except Exception:  # noqa: BLE001
        return False


def _parse_git_log(log_output: str) -> List[MinedCommit]:
    """Parse ``git log --format=...`` output into MinedCommit objects."""
    commits: List[MinedCommit] = []
    current: Optional[MinedCommit] = None
    for line in log_output.splitlines():
        if line.startswith("COMMIT "):
            if current is not None:
                commits.append(current)
            current = MinedCommit(sha=line[7:].strip())
        elif current is not None:
            if line.startswith("AUTHOR "):
                current.author = line[7:].strip()
            elif line.startswith("DATE "):
                try:
                    ts = int(line[5:].strip())
                    current.date = datetime.fromtimestamp(ts, tz=timezone.utc)
                except ValueError:
                    current.date = datetime.now(timezone.utc)
            elif line.startswith("MESSAGE "):
                current.message = line[8:].strip()
            elif line.startswith("FILE "):
                current.files.append(line[5:].strip())
            elif line.startswith("STAT "):
                # Format: "STAT path\tadds\tdels"
                parts = line[5:].split("\t")
                if len(parts) == 3:
                    path, adds, dels = parts
                    current.diff_stat[path] = int(adds) + int(dels)
    if current is not None:
        commits.append(current)
    return commits


def _fetch_commits(project_root: str, n: int = 50) -> List[MinedCommit]:
    """Fetch the last ``n`` commits with metadata and diff stats."""
    fmt = (
        "COMMIT %H%n"
        "AUTHOR %an%n"
        "DATE %ct%n"
        "MESSAGE %s%n"
    )
    log = _run_git(["log", f"--pretty=format:{fmt}", "--name-only", f"-n", str(n)], project_root)

    # Build commits from the log.
    commits = []
    current: Optional[MinedCommit] = None
    in_files = False
    for line in log.splitlines():
        if line.startswith("COMMIT "):
            if current is not None:
                commits.append(current)
            current = MinedCommit(sha=line[7:].strip())
            in_files = False
        elif current is not None:
            if line.startswith("AUTHOR "):
                current.author = line[7:].strip()
            elif line.startswith("DATE "):
                try:
                    ts = int(line[5:].strip())
                    current.date = datetime.fromtimestamp(ts, tz=timezone.utc)
                except ValueError:
                    current.date = datetime.now(timezone.utc)
            elif line.startswith("MESSAGE "):
                current.message = line[8:].strip()
            elif line == "":
                in_files = True
            elif in_files:
                current.files.append(line.strip())
    if current is not None:
        commits.append(current)

    # Fetch diff stats per commit.
    for c in commits:
        try:
            stat = _run_git(["show", "--format=", "--stat", c.sha], project_root)
            for line in stat.splitlines():
                m = re.match(r"^(.+?)\s+\|\s+(\d+)\s+[+-]*$", line)
                if m:
                    c.diff_stat[m.group(1).strip()] = int(m.group(2))
        except Exception:  # noqa: BLE001
            pass

    return commits


def _revert_ratio(earlier: MinedCommit, later_sha: str, project_root: str) -> float:
    """Return the fraction of ``earlier``'s changed lines reverted by ``later_sha``."""
    try:
        diff = _run_git(["diff", "--stat", f"{earlier.sha}..{later_sha}"], project_root)
    except Exception:  # noqa: BLE001
        return 0.0
    total_reverted = 0
    for line in diff.splitlines():
        m = re.match(r"^(.+?)\s+\|\s+(\d+)\s+[+-]*$", line)
        if m:
            path = m.group(1).strip()
            if path in earlier.files:
                total_reverted += int(m.group(2))
    earlier_lines = sum(earlier.diff_stat.values()) or 1
    return total_reverted / earlier_lines


def label_commits(
    commits: List[MinedCommit],
    project_root: str,
    *,
    window_hours: float = 48.0,
) -> List[MinedCommit]:
    """Label commits using the heuristic described in the module docstring."""
    if not commits:
        return commits

    # Index commits by SHA for fast lookup.
    by_sha = {c.sha: c for c in commits}

    for i, commit in enumerate(commits):
        # We can only label commits that still appear in the fetched window.
        # The first commit in the list is the most recent (HEAD).
        label = "positive"
        reason = "no follow-up fix/revert within window"

        for later in commits[:i]:
            if not set(commit.files) & set(later.files):
                continue
            hours = (later.date - commit.date).total_seconds() / 3600.0
            if hours > window_hours:
                continue
            if _LABEL_RE.search(later.message):
                label = "negative"
                reason = f"followed by {later.sha[:8]} ({later.message}) within {hours:.1f}h"
                break
            ratio = _revert_ratio(commit, later.sha, project_root)
            if ratio > 0.30:
                label = "negative"
                reason = f"followed by {later.sha[:8]} reverting {ratio:.0%} of lines within {hours:.1f}h"
                break

        commit.label = label
        commit.label_reason = reason

    return commits


def extract_features(commit: MinedCommit) -> Dict[str, Any]:
    """Extract simple features from a mined commit.

    Phase-4 MVP: surface-level features only. A full implementation would run
    the contract/oracle/PRM gates against the commit's diff and record their
    outputs here.
    """
    message = commit.message.lower()
    return {
        "files_changed": len(commit.files),
        "lines_changed": sum(commit.diff_stat.values()),
        "has_test_keyword": bool(re.search(r"\b(test|spec)\b", message)),
        "has_refactor_keyword": bool(re.search(r"\b(refactor|cleanup)\b", message)),
        "has_fix_keyword": bool(_LABEL_RE.search(message)),
    }


def mine(project_root: str, n: int = 50) -> List[MinedCommit]:
    """Mine and label the last ``n`` commits from ``project_root``."""
    if not _is_git_repo(project_root):
        return []
    commits = _fetch_commits(project_root, n=n)
    commits = label_commits(commits, project_root)
    for c in commits:
        c.features = extract_features(c)
    return commits


__all__ = [
    "MinedCommit",
    "mine",
    "label_commits",
    "extract_features",
]
