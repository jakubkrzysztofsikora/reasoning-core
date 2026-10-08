"""Findings ledger for the dup advisory: findings persist past the edit that
raised them so the Stop hook can resurface them at a boundary.

One append-only JSONL per repo, under the cache root (``$RC_CACHE_DIR`` else
``~/.cache/reasoning-core``) -- outside the repo tree, so it never shows up in
``git status``. Rows are whole findings; on load the latest row per ``id`` wins.

Statuses: ``open`` (not yet acted on), ``surfaced`` (shown at a Stop),
``deferred`` (the agent recorded a reason to leave it), ``resolved``
(re-validation found the duplicate gone).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

STATUSES = ("open", "surfaced", "deferred", "resolved")


class LedgerError(RuntimeError):
    """The ledger could not be written."""


def _cache_dir_default() -> Path:
    override = os.environ.get("RC_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "reasoning-core"


def ledger_path(repo_root: str, cache_dir: str | None = None) -> Path:
    base = Path(cache_dir) if cache_dir else _cache_dir_default()
    key = hashlib.sha256(os.path.abspath(repo_root).encode("utf-8")).hexdigest()[:16]
    return base / f"dup-findings.{key}.jsonl"


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
    """Build a finding row. The id depends on the added function and the set of
    matched sites (not their order or line numbers), so the same duplication
    seen on a later edit maps to the same row."""
    sites_key = sorted(f"{m['path']}::{m['name']}" for m in matches)
    raw = json.dumps([added_path, added_name, sites_key])
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
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        raise LedgerError(f"cannot write dup findings ledger {path}") from exc


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Latest row per finding id. A corrupt line is skipped but reported on
    stderr -- one bad line must not hide every other finding."""
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                print(f"dup-findings: corrupt ledger line {n} in {path}, skipped", file=sys.stderr)
                continue
            rows[row["id"]] = row
    return rows


def record(path: Path, finding: dict[str, Any]) -> None:
    """Upsert a finding. A deferred finding stays deferred when the same
    duplication is seen again -- the agent already gave its reason."""
    existing = load(path).get(finding["id"])
    if existing and existing["status"] == "deferred":
        return
    _append(path, finding)


def set_status(path: Path, finding_id: str, status: str, reason: str | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if status == "deferred" and not (reason or "").strip():
        raise ValueError("deferring a finding needs a non-empty reason")
    rows = load(path)
    if finding_id not in rows:
        raise KeyError(finding_id)
    _append(path, dict(rows[finding_id], status=status, reason=reason, ts=time.time()))
