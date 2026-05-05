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


def _emit_additional_context(blurb: str) -> None:
    """Print the JSON shape Claude Code expects for additionalContext."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": blurb,
        }
    }
    sys.stdout.write(json.dumps(out))


def main() -> None:
    if os.environ.get("RC_LANG_LOCK") != "1":
        sys.exit(0)
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
    age = time.time() - float(data.get("created_ts", 0))
    if not blurb or age > 3600:
        try:
            anchor_path.unlink()
        except OSError:
            pass
        sys.exit(0)
    _emit_additional_context(blurb)
    try:
        anchor_path.unlink()  # consumed-on-read
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
