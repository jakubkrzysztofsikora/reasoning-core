#!/usr/bin/env python3
"""Idempotent backfill: tag legacy untagged audit-log rows as ``schema_version=1``.

Audit rows written before versioning shipped have no ``schema_version`` field.
This script reads each ``.jsonl`` / ``.jsonl.gz`` file under the audit root,
adds ``"schema_version": 1`` to any row missing the field, and rewrites the
file atomically. Rows that already carry ``schema_version`` (any value) are
left untouched, so the operation is safely re-runnable.

This is a one-way backfill, not a schema migration. The live writer in
``src/hooks/audit_log.py`` emits ``SCHEMA_VERSION = 3`` (the current schema);
this script does not rewrite v1 rows into v3 shape -- it only records that
they were authored under the pre-versioning schema.

Usage:
    python3 scripts/backfill_audit_log_schema_version.py --dry-run
    python3 scripts/backfill_audit_log_schema_version.py --apply
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path


def _iter_log_files(base: Path):
    """Yield all .jsonl and .jsonl.gz files under base."""
    if not base.is_dir():
        return
    for root, _dirs, files in os.walk(str(base)):
        for fn in files:
            if fn.endswith(".jsonl") or fn.endswith(".jsonl.gz"):
                yield Path(root) / fn


def _migrate_file(path: Path, apply: bool) -> tuple[int, int]:
    """Return (scanned, migrated) counts for a single file."""
    opener = gzip.open if str(path).endswith(".gz") else open
    lines: list[str] = []
    scanned = 0
    migrated = 0
    try:
        with opener(str(path), "rt", encoding="utf-8") as fh:
            for line in fh:
                scanned += 1
                line = line.rstrip("\n")
                if not line:
                    lines.append(line)
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    lines.append(line)
                    continue
                if not isinstance(row, dict):
                    lines.append(line)
                    continue
                if "schema_version" not in row:
                    row["schema_version"] = 1
                    migrated += 1
                    lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
                else:
                    lines.append(line)
    except OSError as exc:
        print(f"  SKIP (read error): {path}: {exc}", file=sys.stderr)
        return scanned, migrated

    if migrated > 0 and apply:
        try:
            # Atomic write: tmp -> rename
            tmp = path.with_suffix(path.suffix + ".tmp")
            with opener(str(tmp), "wt", encoding="utf-8") as fh:
                for ln in lines:
                    fh.write(ln + "\n")
            tmp.replace(path)
        except OSError as exc:
            print(f"  FAIL (write error): {path}: {exc}", file=sys.stderr)
            return scanned, 0

    return scanned, migrated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill schema_version=1 on legacy untagged audit-log rows"
    )
    parser.add_argument("--base", default="/tmp/rc-events",
                        help="Base directory for audit log files")
    parser.add_argument("--apply", action="store_true",
                        help="Apply migration (default: dry-run)")
    args = parser.parse_args()

    base = Path(args.base)
    total_scanned = 0
    total_migrated = 0

    for path in _iter_log_files(base):
        scanned, migrated = _migrate_file(path, args.apply)
        if scanned > 0:
            action = "MIGRATED" if (migrated > 0 and args.apply) else "would migrate"
            print(f"  {action}: {path} ({migrated}/{scanned} rows)")
            total_scanned += scanned
            total_migrated += migrated

    print(f"\nTotal: {total_migrated}/{total_scanned} rows "
          f"{'migrated' if args.apply else 'would be migrated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
