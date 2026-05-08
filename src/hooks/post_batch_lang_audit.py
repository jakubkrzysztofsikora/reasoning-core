#!/usr/bin/env python3
"""PostToolUse hook: rolling extension-distribution audit.

Reads recent audit rows for this session, computes the histogram of file
extensions written, and if the share of NON-declared-language extensions
exceeds RC_LANG_AUDIT_THRESHOLD (default 0.20) emits an additionalContext
warning into the agent's next turn — soft signal, no blocking.

Honored under RC_LANG_LOCK=1. Cheap: reads only today's audit dir.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _session_manifest as _sm  # type: ignore  # noqa: E402


def _today_dir() -> Path:
    root = Path(os.environ.get(
        "RC_AUDIT_ROOT",
        os.path.expanduser("~/.local/share/reasoning-core/events"),
    ))
    return root / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session_id() -> str:
    try:
        from src.hooks import _host_env  # type: ignore
        return _host_env.session_id()
    except Exception:  # noqa: BLE001
        return os.environ.get("CLAUDE_SESSION_ID") or ""


def _emit_advisory(text: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(out))


def main() -> None:
    if os.environ.get("RC_LANG_LOCK") != "1":
        sys.exit(0)
    # Default 0.33 — anything below 1/3 cross-language is plausibly polyglot
    # tooling. Reviewer-flagged: 0.20 was too noisy (10cs+3py = 0.23 → warn).
    try:
        threshold = float(os.environ.get("RC_LANG_AUDIT_THRESHOLD", "0.33"))
    except ValueError:
        threshold = 0.33
    try:
        from src.hooks import _host_env  # type: ignore
        cwd = str(_host_env.project_dir())
    except Exception:  # noqa: BLE001
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    task_spec = os.environ.get("RC_TASK_SPEC") or ""
    key = _sm.manifest_key(cwd, task_spec)
    mani = _sm.load(key)
    declared = (mani or {}).get("declared_language")
    if not declared:
        sys.exit(0)
    today = _today_dir()
    sid = _session_id()
    if not today.exists() or not sid:
        sys.exit(0)
    counts: Counter = Counter()
    log = today / f"{sid}.jsonl"
    if not log.exists():
        sys.exit(0)
    try:
        with open(log, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                fp = row.get("file_path") or ""
                lang = _sm.language_for_path(fp)
                if lang:
                    counts[lang] += 1
    except OSError:
        sys.exit(0)
    total = sum(counts.values())
    if total < 5:
        sys.exit(0)
    other = sum(n for lang, n in counts.items() if lang != declared)
    ratio = other / total
    if ratio > threshold:
        _emit_advisory(
            f"Note: {ratio:.0%} of files written this session are NOT in the "
            f"declared language family ({declared}). Verify task alignment."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
