#!/usr/bin/env python3
"""Compute operator-override survival ratio from audit log.

Scans audit events, finds ``allowed_via_override`` rows with ``git_head``
fields, and checks whether the file content at override time matches the
current file content. An override "survives" if the file at override HEAD
has identical content to the current checked-out version.

Computes two metrics:
  - survival_ratio: overrides whose content is still in the tree today.
  - reverted_ratio: overrides whose content was later changed/reverted.

Usage:
    python3 scripts/compute_override_survival.py [--days 30] [--audit-root PATH]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _audit_root() -> str:
    return os.environ.get(
        "RC_AUDIT_ROOT",
        os.path.expanduser("~/.local/share/reasoning-core/events"),
    )


def _iter_events(audit_root: Path, days: int):
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    if not audit_root.is_dir():
        return
    for day_dir in sorted(
        audit_root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")
    ):
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day < cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        for f in day_dir.iterdir():
            opener = gzip.open if f.name.endswith(".gz") else open
            try:
                with opener(f, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except (ValueError, TypeError):
                            continue
            except OSError:
                continue


def _file_content_at_commit(git_head: str, file_path: str, repo_root: Path) -> Optional[str]:
    """Return file content at a specific commit, or None."""
    try:
        r = subprocess.run(
            ["git", "show", f"{git_head}:{file_path}"],
            capture_output=True, text=True, timeout=5, cwd=str(repo_root),
        )
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    return None


def _current_file_content(file_path: str, repo_root: Path) -> Optional[str]:
    """Return current file content on disk, or None."""
    fpath = repo_root / file_path
    try:
        if fpath.is_file():
            return fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


def _resolve_repo_root() -> Optional[Path]:
    """Find git repo root from cwd."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def compute_survival(audit_root_path: str, days: int) -> dict:
    audit_root = Path(audit_root_path)
    repo_root = _resolve_repo_root()

    overrides = []
    for ev in _iter_events(audit_root, days):
        if ev.get("decision") != "allowed_via_override":
            continue
        git_head = (ev.get("extra") or {}).get("git_head") if isinstance(ev.get("extra"), dict) else ev.get("git_head")
        if not git_head or not ev.get("file_path"):
            continue
        overrides.append({
            "file_path": ev["file_path"],
            "git_head": git_head,
            "reason": ev.get("reason", ""),
            "ts": ev.get("ts", ""),
        })

    if not overrides:
        return {
            "status": "no_overrides",
            "message": f"No allowed_via_override events with git_head found in last {days} days.",
            "total_overrides": 0,
        }

    survived = 0
    reverted = 0
    unknown = 0
    details = []

    for ov in overrides:
        fp = ov["file_path"]
        gh = ov["git_head"]
        if repo_root:
            content_at_override = _file_content_at_commit(gh, fp, repo_root)
            content_current = _current_file_content(fp, repo_root)
        else:
            content_at_override = None
            content_current = None

        if content_at_override is not None and content_current is not None:
            if content_at_override == content_current:
                survived += 1
                details.append({**ov, "verdict": "survived"})
            else:
                reverted += 1
                details.append({**ov, "verdict": "reverted"})
        else:
            unknown += 1
            details.append({**ov, "verdict": "unknown"})

    total = len(overrides)
    return {
        "status": "ok",
        "total_overrides": total,
        "survived": survived,
        "reverted": reverted,
        "unknown": unknown,
        "survival_ratio": round(survived / max(1, total), 4),
        "reverted_ratio": round(reverted / max(1, total), 4),
        "repo_root": str(repo_root) if repo_root else None,
        "details": details,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compute operator-override survival ratio from audit log",
    )
    p.add_argument("--days", type=int, default=30, help="Days of audit log to scan")
    p.add_argument("--audit-root", default=_audit_root(), help="Audit log root")
    p.add_argument("--json", action="store_true", help="Output full JSON with details")
    args = p.parse_args()

    result = compute_survival(args.audit_root, args.days)

    if result.get("status") == "no_overrides":
        print(result["message"])
        return 0

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Override Survival (last {args.days} days):")
        print(f"  total overrides:      {result['total_overrides']}")
        print(f"  survived (unchanged): {result['survived']}")
        print(f"  reverted (changed):   {result['reverted']}")
        print(f"  unknown (deleted/etc):{result.get('unknown', 0)}")
        print(f"  survival ratio:       {result['survival_ratio']:.2%}")
        print(f"  reverted ratio:       {result['reverted_ratio']:.2%}")
        if result.get("repo_root"):
            print(f"  repo root:            {result['repo_root']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
