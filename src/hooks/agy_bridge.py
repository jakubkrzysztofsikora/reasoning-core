#!/usr/bin/env python3
"""Antigravity CLI (agy) hook bridge.

agy speaks a different hook contract than Claude Code: camelCase payloads,
``{"decision": "allow|deny|ask|force_ask"}`` for PreToolUse, ``{}`` for
PostToolUse, and feedback via PreInvocation ``injectSteps``. This bridge
translates agy payloads into the Claude-shaped payloads the rc guards and
receipt hooks already consume, and translates their results back.

Modes (argv[1]): ``pre_edit``, ``pre_command``, ``post_edit``,
``post_command``, ``pre_invocation``. Never exits non-zero; guard failures
fail open (allow), receipt failures are swallowed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from src.hooks import _episodes as _ep  # noqa: E402
from src.hooks import audit_log  # noqa: E402

_RC_HOOKS = {
    "pre_edit": "pre_edit_guard.py",
    "pre_command": "pre_bash_guard.py",
    "post_edit": "post_edit_check.py",
    "post_command": "post_bash_verification.py",
}

_EDIT_TOOLS = {"write_to_file", "replace_file_content"}
_COMMAND_TOOLS = {"run_command"}
_REASON_LIMIT = 4000
_FEEDBACK_FRESHNESS_S = 600.0


def _payload() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _tool(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    call = payload.get("toolCall")
    if not isinstance(call, dict):
        return "", {}
    args = call.get("args")
    return str(call.get("name") or ""), args if isinstance(args, dict) else {}


def _conversation_id(payload: dict[str, Any]) -> str:
    return str(payload.get("conversationId") or "")


def _project_root(path: str) -> str:
    start = Path(path).expanduser()
    base = start if start.is_dir() else start.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return str(Path(result.stdout.strip()).resolve())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return str(base.resolve())
    except OSError:
        return str(base)


def _exit_code_from_error(error: str) -> int:
    match = re.search(r"exit status (\d+)", error or "")
    if match:
        return int(match.group(1))
    return 1 if (error or "").strip() else 0


def translate(payload: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """Map an agy payload to the Claude-shaped payload rc hooks expect."""
    tool, args = _tool(payload)
    session = _conversation_id(payload)
    if mode in ("pre_edit", "post_edit"):
        if tool not in _EDIT_TOOLS:
            return None
        target = str(args.get("TargetFile") or "")
        if not target:
            return None
        project = _project_root(target)
        if tool == "write_to_file":
            tool_input = {"file_path": target, "content": args.get("CodeContent", "")}
            tool_name = "Write"
        else:
            tool_input = {
                "file_path": target,
                "old_string": args.get("TargetContent", ""),
                "new_string": args.get("ReplacementContent", ""),
            }
            tool_name = "Edit"
        return {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "cwd": project,
            "session_id": session,
        }
    if mode in ("pre_command", "post_command"):
        if tool not in _COMMAND_TOOLS:
            return None
        cwd = str(args.get("Cwd") or os.getcwd())
        translated = {
            "tool_name": "Bash",
            "tool_input": {"command": str(args.get("CommandLine") or "")},
            "cwd": _project_root(cwd),
            "session_id": session,
        }
        if mode == "post_command":
            translated["tool_response"] = {
                "exit_code": _exit_code_from_error(str(payload.get("error") or ""))
            }
        return translated
    return None


def guard_decision(returncode: int, stdout: str, stderr: str) -> dict[str, str]:
    """Translate an rc guard result into agy's PreToolUse decision contract."""
    if returncode == 2:
        reason = (stderr or stdout or "blocked by reasoning-core guard").strip()
        return {"decision": "deny", "reason": reason[:_REASON_LIMIT]}
    return {"decision": "allow"}


def _run_hook(mode: str, translated: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    hook = _HOOKS_DIR / _RC_HOOKS[mode]
    env = {
        **os.environ,
        "RC_SESSION_ID": str(translated.get("session_id") or ""),
        "RC_PROJECT_DIR": str(translated.get("cwd") or ""),
        "CLAUDE_PROJECT_DIR": str(translated.get("cwd") or ""),
    }
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(translated),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )


def _fresh(ts: str) -> bool:
    try:
        parsed = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    age = (_dt.datetime.now(_dt.timezone.utc) - parsed).total_seconds()
    return 0 <= age <= _FEEDBACK_FRESHNESS_S


def feedback_message(payload: dict[str, Any]) -> str:
    """Build an ephemeral reminder from the latest unresolved failed episode."""
    session = _conversation_id(payload)
    if not session:
        return ""
    try:
        episodes = _ep.build_episodes(
            Path(audit_log._AUDIT_ROOT), days=1, session_id=session,
            include_synthetic=True,
        )
    except Exception:  # noqa: BLE001 - feedback is best-effort
        return ""
    failed = [
        episode for episode in episodes
        if episode["final_status"] in ("failed_pending_repair", "failed_budget_exhausted")
        and _fresh(episode.get("ended_ts", ""))
    ]
    if not failed:
        return ""
    episode = max(failed, key=lambda item: _ep._ts_key(item.get("ended_ts", "")))
    state = _ep.repair_state(episode)
    failed_checks = [
        check for check in episode.get("checks", [])
        if check.get("verification_status") == "failed"
    ]
    first_error = ""
    if failed_checks:
        first_error = str(failed_checks[0].get("verification_kind") or "check")
    if state["status"] == "abstain":
        repair = f"Repair: budget exhausted ({state['max_attempts']} attempts)"
        next_action = "stop and ask the user; do not attempt further repairs"
    else:
        repair = f"Repair: attempt {state['attempt']} of {state['max_attempts']} — fix and rerun"
        next_action = "repair, then rerun"
    return "\n".join([
        "Verification: FAILED",
        f"Check: {first_error}",
        f"File: {episode.get('file_path_rel') or episode.get('file_path') or '?'}",
        repair + ".",
        f"Next action: {next_action}.",
    ])


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    mode = args[0] if args else ""
    if mode not in _RC_HOOKS and mode != "pre_invocation":
        sys.stdout.write("{}\n")
        return 0
    payload = _payload()
    try:
        if mode == "pre_invocation":
            message = feedback_message(payload)
            if message:
                sys.stdout.write(json.dumps({
                    "injectSteps": [{"ephemeralMessage": message}]
                }) + "\n")
            else:
                sys.stdout.write("{}\n")
            return 0
        translated = translate(payload, mode)
        if translated is None:
            if mode.startswith("pre_"):
                sys.stdout.write('{"decision": "allow"}\n')
            else:
                sys.stdout.write("{}\n")
            return 0
        result = _run_hook(mode, translated)
        if mode.startswith("pre_"):
            sys.stdout.write(json.dumps(
                guard_decision(result.returncode, result.stdout, result.stderr)
            ) + "\n")
        else:
            sys.stdout.write("{}\n")
        return 0
    except Exception:  # noqa: BLE001 - never break the host loop
        if mode.startswith("pre_") and mode != "pre_invocation":
            sys.stdout.write('{"decision": "allow"}\n')
        else:
            sys.stdout.write("{}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
