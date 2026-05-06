#!/usr/bin/env python3
"""UserPromptSubmit hook (P3 Invariant 3 sibling).

Reads the post-compact anchor blurb (if present) and emits it via
hookSpecificOutput.additionalContext — Claude Code's supported mechanism
for injecting context into the next turn. Fires on every user prompt
submission; clears the anchor after first use to avoid spamming.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _session_manifest as _sm  # type: ignore  # noqa: E402


def _read_event_name() -> str:
    """Hook stdin payload carries hook_event_name. Fallback to UserPromptSubmit."""
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            evt = data.get("hook_event_name") or data.get("hookEventName")
            if isinstance(evt, str) and evt:
                return evt
    except (ValueError, OSError):
        pass
    return "UserPromptSubmit"


def _emit_additional_context(blurb: str, event_name: str) -> None:
    """Print the JSON shape Claude Code expects for additionalContext."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": blurb,
        }
    }
    sys.stdout.write(json.dumps(out))


def main() -> None:
    if os.environ.get("RC_LANG_LOCK") != "1":
        sys.exit(0)
    event_name = _read_event_name()
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    task_spec = os.environ.get("RC_TASK_SPEC") or ""
    key = _sm.manifest_key(cwd, task_spec)
    state_dir = Path(os.path.expanduser("~/.local/state/reasoning-core/sessions"))
    anchor_path = state_dir / f"{key}.anchor.json"
    if not anchor_path.exists():
        sys.exit(0)
    try:
        data = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit(0)
    blurb = data.get("blurb")
    try:
        age = time.time() - float(data.get("created_ts") or 0)
    except (TypeError, ValueError):
        age = 9999
    # Cross-session bleed protection: only consume the anchor if it was
    # written by the SAME session_id that's now consuming it. Otherwise
    # the anchor leaks across `claude --resume` boundaries within the
    # 1h TTL.
    anchor_sid = data.get("session_id") or ""
    cur_sid = os.environ.get("CLAUDE_SESSION_ID") or ""
    if anchor_sid and cur_sid and anchor_sid != cur_sid:
        sys.exit(0)
    if not blurb or age > 3600:
        try:
            anchor_path.unlink()
        except OSError:
            pass
        sys.exit(0)
    _emit_additional_context(blurb, event_name)
    try:
        anchor_path.unlink()  # consumed-on-read
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
