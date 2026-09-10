#!/usr/bin/env python3
"""Record deterministic verification results reported by a Bash PostToolUse hook.

The hook never reruns commands. It accepts only an explicit exit status from the
host payload, finds the most recent guarded decision in the same session/project,
and records a decision-linked verification receipt. Missing status or a command
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

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
import audit_log  # type: ignore  # noqa: E402

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


def _latest_decision(session_id: str, project_dir: str) -> str | None:
    root = Path(getattr(audit_log, "_AUDIT_ROOT", ""))
    today = getattr(audit_log, "_today", lambda: "")()
    path = root / today / f"{session_id}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("project_dir") != str(Path(project_dir).resolve()):
            continue
        if event.get("event_type") in {"verification_recorded", "session_outcome_recorded"}:
            continue
        if event.get("decision_id") and event.get("tool_name") in {"Edit", "Write", "gate_edit", "pre_edit_guard"}:
            return str(event["decision_id"])
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
    decision_id = _latest_decision(session_id, project_dir)
    if not decision_id:
        return
    rc_cli = _PROJECT_ROOT / "src" / "rc_cli.py"
    args = [
        sys.executable, str(rc_cli), "record-verification",
        "--kind", kind, "--status", "passed" if exit_code == 0 else "failed",
        "--exit-code", str(exit_code), "--decision-id", decision_id,
        "--session-id", session_id, "--project-dir", project_dir,
        "--command", command[:2048],
    ]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in (str(_PROJECT_ROOT), os.environ.get("PYTHONPATH", "")) if p)}
    try:
        subprocess.run(args, cwd=project_dir, env=env, timeout=10, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    main()
