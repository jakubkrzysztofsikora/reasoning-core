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


def _read_payload() -> dict:
    """Read full hook stdin payload once; reused for event name and session_id."""
    try:
        from src.hooks.adapters import claude as _claude_adapter  # type: ignore
        env = _claude_adapter.parse_stdin("SessionStart")
        if env.raw:
            return dict(env.raw)
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
    except (ValueError, OSError):
        pass
    return {}


def _event_name(payload: dict) -> str:
    evt = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(evt, str) and evt:
        return evt
    return "UserPromptSubmit"


def _session_id_from(payload: dict) -> str:
    """Reviewer fix: CLAUDE_SESSION_ID env is NOT exported by Claude Code.
    The session_id lives on the stdin payload only. Fall back to env if
    payload is empty (backwards-compat with hand-fired tests)."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    return os.environ.get("CLAUDE_SESSION_ID") or ""


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
    payload = _read_payload()
    event_name = _event_name(payload)
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
    # Cross-session bleed protection: anchor.session_id must match the
    # current session's session_id (read from STDIN payload, not env —
    # Claude Code doesn't export CLAUDE_SESSION_ID).
    anchor_sid = data.get("session_id") or ""
    cur_sid = _session_id_from(payload)
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
