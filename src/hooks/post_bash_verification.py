#!/usr/bin/env python3
"""Record deterministic verification results reported by a Bash PostToolUse hook.

The hook never reruns commands. It accepts only an explicit exit status from the
host payload, uses a host-supplied parent ID when present, and otherwise
records a session-level verification receipt. Missing status or a command
that is not recognizably a check is ignored rather than creating synthetic evidence.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_CHECKS = (
    ("test", re.compile(r"(?:^|\s)(?:pytest|mvn\s+test|gradle\s+test|cargo\s+test|go\s+test|dotnet\s+test|(?:npm|pnpm|yarn)\s+(?:run\s+)?test)\b")),
    ("lint", re.compile(r"(?:^|\s)(?:ruff|eslint|biome|golangci-lint|clippy)\b")),
    ("typecheck", re.compile(r"(?:^|\s)(?:mypy|pyright|tsc)\b")),
    ("build", re.compile(r"(?:^|\s)(?:make\s+(?:build|check)|cargo\s+build|go\s+build|dotnet\s+build|npm\s+run\s+build|pnpm\s+build|yarn\s+build)\b")),
)


def _payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return tool_input["command"]
    return ""


def _exit_code(payload: dict[str, Any]) -> int | None:
    values: list[Any] = [payload.get("exit_code"), payload.get("exitCode")]
    for key in ("tool_response", "tool_result", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            values.extend((value.get("exit_code"), value.get("exitCode"), value.get("status")))
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _kind(command: str) -> str | None:
    for kind, pattern in _CHECKS:
        if pattern.search(command):
            return kind
    return None


def _explicit_decision_id(payload: dict[str, Any]) -> str | None:
    """Return only a host-supplied parent ID; never infer one by recency."""
    for key in ("decision_id", "parent_decision_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("decision_id", "parent_decision_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def main() -> None:
    payload = _payload()
    if payload.get("tool_name") not in (None, "Bash"):
        return
    command = _command(payload)
    kind = _kind(command)
    exit_code = _exit_code(payload)
    if not command or kind is None or exit_code is None:
        return
    project_dir = str(Path(payload.get("cwd") or os.getcwd()).resolve())
    session_id = str(payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "")
    if not session_id:
        return
    decision_id = _explicit_decision_id(payload)
    rc_cli = _PROJECT_ROOT / "src" / "rc_cli.py"
    args = [
        sys.executable, str(rc_cli), "record-verification",
        "--kind", kind, "--status", "passed" if exit_code == 0 else "failed",
        "--exit-code", str(exit_code),
        "--association-type", "explicit_decision" if decision_id else "session_level",
        "--session-id", session_id, "--project-dir", project_dir,
        "--command", command[:2048],
    ]
    if decision_id:
        args.extend(("--decision-id", decision_id))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in (str(_PROJECT_ROOT), os.environ.get("PYTHONPATH", "")) if p)}
    try:
        subprocess.run(args, cwd=project_dir, env=env, timeout=10, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    main()
