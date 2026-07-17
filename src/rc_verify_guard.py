#!/usr/bin/env python3
"""Minimal guard-hash verifier (no self-reference).

Reads the stored guard hashes and compares them against the current
contents of guard files. Does NOT call any other rc_cli code path, so
tampering with `rc_cli.py` cannot bypass the verifier.

Used by `rc guard-hash` (operator) and Stop hooks (auto-verify before
session end). Exit 0 if all files match, 2 if any file is TAMPERED or
the store is unreadable.

Run with the same Python and project layout as `rc_cli.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORE = Path(os.path.expanduser("~/.local/state/reasoning-core/guard_hashes.json"))


def _store_path() -> Path:
    return Path(os.environ.get("RC_STATE_DIR", str(DEFAULT_STORE.parent))) / "guard_hashes.json"


def _verify(file_path: str, store_path: Path | None = None) -> tuple[bool, str]:
    """Return (ok, reason). reason is one of:
      - "match" when verified
      - "missing_file" when guard file is absent
      - "store_missing" when no store
      - "store_corrupt" when store unreadable
      - "not_registered" when file not in store
      - "mismatch" when hash differs
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        return (False, "missing_file")
    store = store_path or _store_path()
    if not store.is_file():
        return (False, "store_missing")
    try:
        records = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (False, "store_corrupt")
    if not isinstance(records, dict):
        return (False, "store_corrupt")
    key = str(path)
    if key not in records:
        return (False, "not_registered")
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    if records[key] != current:
        return (False, "mismatch")
    return (True, "match")


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Verify guard file hashes (no self-reference)")
    p.add_argument("--store", type=Path, default=None, help="Path to guard hash store")
    p.add_argument("--reason-only", action="store_true", help="print the failure reason and exit non-zero")
    args = p.parse_args()

    guard_files = [
        REPO_ROOT / "src" / "hooks" / "pre_edit_guard.py",
        REPO_ROOT / "src" / "hooks" / "_dispatch.py",
        REPO_ROOT / "src" / "hooks" / "_guard_paths.py",
        REPO_ROOT / "src" / "hooks" / "_kill_switches.py",
        REPO_ROOT / "src" / "hooks" / "_magic_comments.py",
        REPO_ROOT / "src" / "hooks" / "_rule_engine.py",
        REPO_ROOT / "src" / "hooks" / "audit_log.py",
        REPO_ROOT / "src" / "rc_cli.py",
    ]

    store = args.store or _store_path()
    all_ok = True
    last_reason = ""
    for g in guard_files:
        ok, reason = _verify(str(g), store)
        if not ok:
            all_ok = False
            last_reason = f"{g.name}: {reason}"
            if args.reason_only:
                sys.stdout.write(last_reason + "\n")
                return 2
            sys.stderr.write(f"  TAMPERED ({reason})  {g}\n")

    if all_ok:
        sys.stdout.write("guard files match stored hashes\n")
        return 0
    sys.stderr.write(f"TAMPERING DETECTED ({last_reason})\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())