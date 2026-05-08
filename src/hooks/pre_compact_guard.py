#!/usr/bin/env python3
"""PreCompact hook (P3 Invariant 3).

Reviewer correction: Claude Code's PreCompact API does NOT inject
systemMessage into the post-compact turn. Instead this hook persists the
session manifest + a short re-anchor blurb to disk; session_resume_inject
later reads it via UserPromptSubmit/SessionStart-on-resume to feed the
content back into the agent via additionalContext (the supported path).
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


def _read_session_id_from_stdin() -> str:
    """Reviewer-flagged: CLAUDE_SESSION_ID env is NOT exported by Claude Code.
    The session_id comes from the JSON payload on stdin per hook spec."""
    try:
        from src.hooks.adapters import claude as _claude_adapter  # type: ignore
        env = _claude_adapter.parse_stdin("PreCompact")
        if env.session_id:
            return env.session_id
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            sid = data.get("session_id") if isinstance(data, dict) else None
            if isinstance(sid, str) and sid:
                return sid
    except (ValueError, OSError):
        pass
    return os.environ.get("CLAUDE_SESSION_ID") or ""


def main() -> None:
    if os.environ.get("RC_LANG_LOCK") != "1":
        sys.exit(0)
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    task_spec = os.environ.get("RC_TASK_SPEC") or ""
    key = _sm.manifest_key(cwd, task_spec)
    mani = _sm.load(key)
    if not mani:
        sys.exit(0)
    declared = mani.get("declared_language") or "unknown"
    blurb = (
        f"Session task language: {declared}. "
        f"Stay in this language family for production code."
    )
    # Stamp session_id from stdin payload (NOT env). Without this, the
    # bleed-protection in session_resume_inject sees empty anchor.session_id
    # and silently no-ops the cross-session check.
    session_id = _read_session_id_from_stdin()
    state_dir = Path(os.path.expanduser("~/.local/state/reasoning-core/sessions"))
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        anchor_path = state_dir / f"{key}.anchor.json"
        anchor_path.write_text(json.dumps({
            "key": key,
            "session_id": session_id,
            "declared_language": declared,
            "blurb": blurb,
            "created_ts": time.time(),
        }), encoding="utf-8")
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
