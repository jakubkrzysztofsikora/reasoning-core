#!/usr/bin/env python3
"""Claude Code PostToolUse feedback for the host-outcome pilot.

This deliberately permits the write, then returns the same deterministic
rule/contract failure as actionable context. It is the control counterpart to
the production PreToolUse gate, not a production hook configuration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.timing_pilot.check_policy import check_policy


def _payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    payload = _payload()
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    file_path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(file_path, str) or not file_path:
        return 0
    project = Path(payload.get("cwd") or ".").resolve()
    target = Path(file_path)
    try:
        relative = target.resolve().relative_to(project)
    except ValueError:
        return 0
    result = check_policy(project, str(relative))
    if result is None:
        return 0
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "reasoning-core post-write policy feedback: "
                f"{result}. Revise the file to satisfy the repository policy."
            ),
        }
    }) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
