"""Findings ledger for the duplicate check: findings persist past the edit
that raised them so the Stop hook can review them at the end of the session.

One append-only .JSONL file per repo, under the cache root (``$RC_CACHE_DIR`` else
``~/.cache/reasoning-core``) -- outside the repo tree, so it never shows up in
``git status``. Rows are whole findings; on load the latest row per ``id`` wins.

Statuses: ``open`` (not yet acted on), ``deferred`` (the agent recorded a
reason to leave it), ``escalated`` (still open after the Stop block cap; no
longer blocks), ``resolved`` (re-validation found the duplicate gone).
How often a finding has blocked a Stop is counted in its ``blocks`` field.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

STATUSES = ("open", "deferred", "escalated", "resolved")
# Statuses an edit that sees the same duplication again must not undo.
_STICKY = ("deferred", "escalated")


class LedgerError(RuntimeError):
    """The ledger could not be written."""


def _cache_dir_default() -> Path:
    override = os.environ.get("RC_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "reasoning-core"


def resolve_repo_root(start: str) -> str:
    """The real path of the git work tree containing ``start``, else of
    ``start`` itself. One repo gets one key however it was reached (a
    subdirectory, a symlink)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        # silent-ok: not a repo, or git missing -- changed_files() reports the latter.
        pass
    return os.path.realpath(start)


def ledger_path(repo_root: str, cache_dir: str | None = None) -> Path:
    base = Path(cache_dir) if cache_dir else _cache_dir_default()
    key = hashlib.sha256(resolve_repo_root(repo_root).encode("utf-8")).hexdigest()[:16]
    return base / f"dup-findings.{key}.jsonl"


def changed_files(repo_root: str) -> set[str] | None:
    """Repo-relative paths modified or untracked in the working tree, or
    ``None`` when that can't be known (not a git repo, git missing). ``None``
    is reported on stderr so callers never mistake "unknown" for "clean"."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
            cwd=repo_root, capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"dup-findings: changed files unknown ({exc!r})", file=sys.stderr)
        return None
    paths: set[str] = set()
    entries = iter(out.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.add(path)
        if "R" in status or "C" in status:
            next(entries, None)  # rename/copy: the next entry is the old path
    return paths


def committed_source(repo_root: str, rel_path: str) -> str | None:
    """The file as committed at ``HEAD``, or ``None`` when there is none (new
    or untracked file, no commits, not a git repo). A missing git binary is
    reported on stderr; the other cases are expected."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:./{rel_path}"],
            cwd=repo_root, capture_output=True, timeout=10,
            # Read like the edited file is read: bad bytes must not fail the check.
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"dup-findings: committed version unknown ({exc!r})", file=sys.stderr)
        return None
    return result.stdout if result.returncode == 0 else None


def make_finding(
    *,
    added_path: str,
    added_name: str,
    matches: list[dict[str, Any]],
    verdict: str,
    in_diff: bool,
    likely_move: bool,
    test_covered: bool,
) -> dict[str, Any]:
    """Build a finding row. The id depends only on the set of sites, the added
    one included (not their order, line numbers, or which copy was edited), so
    the same duplication seen from any copy maps to the same row. A new copy
    makes a new set, and so a new finding."""
    sites_key = sorted(
        {f"{added_path}::{added_name}"} | {f"{m['path']}::{m['name']}" for m in matches}
    )
    raw = json.dumps(sites_key)
    return {
        "id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
        "added_path": added_path,
        "added_name": added_name,
        "matches": matches,
        "sites": 1 + len(matches),
        "verdict": verdict,
        "in_diff": in_diff,
        "likely_move": likely_move,
        "test_covered": test_covered,
        "status": "open",
        "reason": None,
        "ts": time.time(),
    }


def _append(path: Path, row: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as fh:
            # A write cut short (hook killed) leaves no trailing newline; start
            # on a fresh line so this row isn't glued onto the broken one.
            fh.seek(0, os.SEEK_END)
            lead = b""
            if fh.tell():
                fh.seek(-1, os.SEEK_END)
                lead = b"" if fh.read(1) == b"\n" else b"\n"
            fh.write(lead + (json.dumps(row) + "\n").encode("utf-8"))
    except OSError as exc:
        raise LedgerError(f"cannot write dup findings ledger {path}") from exc


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Latest row per finding id. A corrupt line is skipped but reported on
    stderr -- one bad line must not hide every other finding."""
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if not isinstance(row, dict) or "id" not in row or "status" not in row:
                print(f"dup-findings: corrupt ledger line {n} in {path}, skipped", file=sys.stderr)
                continue
            rows[row["id"]] = row
    return rows


def record(path: Path, finding: dict[str, Any]) -> None:
    """Upsert a finding. A deferred or escalated finding keeps its status when
    the same duplication is seen again, and the Stop block count carries over,
    so editing the file again does not reset the cap."""
    existing = load(path).get(finding["id"])
    if existing and existing["status"] in _STICKY:
        return
    if existing and existing["status"] == "open" and "blocks" in existing and "blocks" not in finding:
        finding = dict(finding, blocks=existing["blocks"])
    if existing and _was_extract(existing):
        # Kept, even past resolution, so the Stop review can tell an extraction
        # (copies reduced to thin wrappers, re-recorded as a NOTE whenever one is
        # edited) from a plain trivial note.
        finding = dict(finding, was_extract=True)
    _append(path, finding)


def _was_extract(row: dict[str, Any]) -> bool:
    return bool(row.get("was_extract")) or row.get("verdict") == "extract"


def set_status(path: Path, finding_id: str, status: str, reason: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if status == "deferred" and not (reason or "").strip():
        raise ValueError("deferring a finding needs a non-empty reason")
    rows = load(path)
    if finding_id not in rows:
        raise KeyError(finding_id)
    _append(path, dict(rows[finding_id], status=status, reason=reason, ts=time.time()))
