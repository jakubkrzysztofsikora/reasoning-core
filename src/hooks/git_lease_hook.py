"""Keep an open worktree lease in the agent's view.

PostToolUse / SessionStart / UserPromptSubmit: inject the lease reminder.
Stop: block the stop while the lease is open (once per stop attempt).
Silent when no lease is open or git state is unreadable.
"""
from __future__ import annotations

import json
import os
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import _git_lease  # type: ignore  # noqa: E402


def _state_exists() -> bool:
    try:
        return _git_lease._lease_state_path(_git_lease.home_root()).exists()
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    event = payload.get("hook_event_name", "")
    try:
        home = _git_lease.home_root()
        if event == "PostToolUse":
            lease = _git_lease.bump_calls(home)
        else:
            lease = _git_lease.active_lease(home)
    except Exception as exc:  # noqa: BLE001
        if event == "Stop" and not payload.get("stop_hook_active") and _state_exists():
            sys.stderr.write(f"[rc] lease state unreadable ({exc}); resolve it or tell the operator.\n")
            return 2
        return 0
    if not lease:
        if event != "Stop" or payload.get("stop_hook_active"):
            return 0
        try:
            stray = [t for t in _git_lease.worktrees(home) if t != home]
        except Exception:  # noqa: BLE001
            return 0
        if stray:
            sys.stderr.write(f"[rc] linked worktree {stray[0]} exists without a lease; merge and remove it, "
                             "or tell the operator why not.\n")
            return 2
        return 0
    text = _git_lease.reminder(home, lease)
    if event == "Stop":
        if payload.get("stop_hook_active"):
            return 0
        sys.stderr.write(text + "\nMerge and remove the worktree before stopping, or tell the operator why not.\n")
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
