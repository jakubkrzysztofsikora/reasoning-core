#!/usr/bin/env python3
"""Stop hook: run rc reconcile to catch MCP-skip scenarios.

Fires at the end of a session (Stop event in Claude Code, or equivalent in
other hosts). Calls `rc reconcile` to diff git working tree against gate_edit
audit rows. If files were written without a gate call, emits an
additionalContext blurb warning the operator.

In advisory mode (RC_MODE=advise): always exit 0, just audit.
In copilot mode (RC_MODE=copilot): exit 2 if MCP-skip detected, forcing the
agent to call gate_edit before the session ends.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _project_dir() -> str:
    return str(
        Path(
            os.environ.get("RC_RUN_DIR")
            or os.environ.get("RC_PROJECT_DIR")
            or os.getcwd()
        ).resolve()
    )


def _run_rc_reconcile(project_dir: str) -> tuple[int, str]:
    """Run `rc reconcile` and return (returncode, stderr)."""
    rc_cli = _PROJECT_ROOT / "src" / "rc_cli.py"
    if not rc_cli.is_file():
        return (0, "")
    try:
        result = subprocess.run(
            [sys.executable, str(rc_cli), "reconcile"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_dir,
        )
        return (result.returncode, result.stderr)
    except Exception:
        return (0, "")


def main() -> None:
    payload = _read_payload()
    project_dir = _project_dir()
    rc_mode = os.environ.get("RC_MODE", "advise").strip().lower()

    rc, stderr = _run_rc_reconcile(project_dir)

    if rc == 0:
        # No MCP-skip detected
        sys.exit(0)

    # MCP-skip detected
    if rc_mode in ("copilot", "autopilot"):
        # In copilot mode, block the session end
        print(
            f"[reasoning-core] MCP-SKIP DETECTED: files written without gate_edit call.\n"
            f"{stderr}\n"
            f"All file writes must go through the gate. Do not bypass.",
            file=sys.stderr,
        )
        sys.exit(2)
    else:
        # Advisory: just log
        print(
            f"[reasoning-core] advisory: MCP-skip detected (use rc reconcile for details)\n"
            f"{stderr}",
            file=sys.stderr,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()