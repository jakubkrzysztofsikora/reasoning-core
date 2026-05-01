#!/usr/bin/env python3
"""Claude Code PostToolUse hook for the Bash tool.

If a Bash command was executed that *looks* like it killed the sidecar
(pkill/kill against s2_core / mcp_reasoner), this hook checks whether
``127.0.0.1:8765`` is still answering /health. If not, it relaunches
``scripts/start-sidecar.sh`` in the background and writes a loud stderr
note so the next Edit/Write hook is reachable again.

The pre_bash_guard already blocks the obvious kill patterns, so this hook
exists as a defense-in-depth layer for everything pre_bash_guard misses
(e.g. an indirect kill via an exec'd subscript).

Exit code is always 0 — a PostToolUse hook is informational; we never
want to kill Claude's session because revival succeeded.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

SIDECAR_HEALTH_URL = os.getenv("S2_URL", "http://127.0.0.1:8765") + "/health"

KILL_HINT_PATTERNS = (
    re.compile(r"\bp?kill(?:all)?\b.*\b(?:s2_core|mcp_reasoner|start-sidecar)\b"),
    re.compile(r"\bkill\b\s+(?:-\d+\s+)?\d+"),  # raw kill <pid> — best-effort
    re.compile(r"\blaunchctl\s+(?:unload|kill)\b"),
)


def _sidecar_alive() -> bool:
    try:
        with urllib.request.urlopen(SIDECAR_HEALTH_URL, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _start_sidecar() -> None:
    """Best-effort relaunch via scripts/start-sidecar.sh, fully detached."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or _guess_project_dir()
    if not project_dir:
        sys.stderr.write("[hybrid-reasoner] revive: cannot locate CLAUDE_PROJECT_DIR\n")
        return
    script = os.path.join(project_dir, "scripts", "start-sidecar.sh")
    if not os.path.isfile(script):
        sys.stderr.write(f"[hybrid-reasoner] revive: script missing at {script}\n")
        return
    log_path = "/tmp/rc-sidecar-revive.log"
    try:
        with open(log_path, "ab") as logf:
            subprocess.Popen(  # noqa: S603 - intentional detached spawn
                ["bash", script],
                cwd=project_dir,
                stdout=logf,
                stderr=logf,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "BACKGROUND": "1"},
            )
        sys.stderr.write(
            "[hybrid-reasoner] revive: relaunched sidecar (log: " + log_path + ")\n"
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[hybrid-reasoner] revive: relaunch failed ({exc})\n")


def _guess_project_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def main() -> None:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        sys.exit(0)
    if not raw:
        sys.exit(0)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    tool_input = payload.get("tool_input") or {}
    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(cmd, str):
        sys.exit(0)

    looked_like_kill = any(p.search(cmd) for p in KILL_HINT_PATTERNS)
    if not looked_like_kill:
        sys.exit(0)

    # Only revive if sidecar actually went down.
    if _sidecar_alive():
        sys.exit(0)

    sys.stderr.write(
        "[hybrid-reasoner] DETECTED sidecar kill via Bash; attempting revival.\n"
        f"  command: {cmd[:200]}\n"
    )
    _start_sidecar()
    sys.exit(0)


if __name__ == "__main__":
    main()
