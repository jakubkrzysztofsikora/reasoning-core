#!/usr/bin/env python3
"""SessionStart hook (P3 Invariant 3 prep).

Snapshots the worktree's file-extension distribution + declares a dominant
language family for this session. Persists the manifest keyed by
(cwd_hash, task_spec_hash) so it survives `claude --resume`.

Honored under RC_LANG_LOCK=1. Reads CLAUDE_PROJECT_DIR for cwd; the task
spec is approximated by the first-line title from any open prompt or the
literal env var RC_TASK_SPEC if set.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _session_manifest as _sm  # type: ignore  # noqa: E402


def main() -> None:
    if os.environ.get("RC_LANG_LOCK") != "1":
        sys.exit(0)
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    task_spec = os.environ.get("RC_TASK_SPEC") or ""
    key = _sm.manifest_key(cwd, task_spec)
    existing = _sm.load(key)
    if existing and (time.time() - float(existing.get("created_ts", 0))) < 86400:
        # Rehydrate: < 24h old, same task_spec_hash → keep manifest.
        sys.exit(0)
    declared, counts = _sm.detect_initial_language(cwd)
    manifest = {
        "key": key,
        "cwd": cwd,
        "cwd_hash": key.split("_", 1)[0] if "_" in key else key[:12],
        "task_spec_hash": key.split("_", 1)[1] if "_" in key else "",
        "created_ts": time.time(),
        "declared_language": declared,
        "framework": None,  # detection deferred
        "ext_distribution": counts,
        "lang_allow": [
            ext.strip() for ext in (os.environ.get("RC_LANG_ALLOW") or "").split(",") if ext.strip()
        ],
    }
    _sm.save(manifest)
    if declared:
        sys.stderr.write(
            f"[hybrid-reasoner] session manifest: declared_language={declared}\n"
        )
    else:
        sys.stderr.write(
            "[hybrid-reasoner] session manifest: declared_language=None "
            "(low-signal worktree; lang-lock will not gate this session)\n"
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
