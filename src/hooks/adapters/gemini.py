"""Gemini CLI stdin -> HookEnvelope adapter.

Verification (2026-05-08, Gemini v0.37.1): Gemini's hook envelope is
Claude-compatible by design (`gemini hooks migrate` migrates Claude
hooks). Tool names are lower_snake_case: ``write_file``, ``edit_file``,
``run_shell_command``, ``task``. We normalise to canonical Claude names
so the dispatch chain treats them uniformly.

Same parse-failure contract as the Claude adapter: NEVER raises.
"""

from __future__ import annotations

import json
import sys
from types import MappingProxyType

from src.hooks import _host_env
from src.hooks._envelope import HookEnvelope

_HOST = "gemini"

GEMINI_TO_CANONICAL = {
    "write_file": "Write",
    "edit_file": "Edit",
    "str_replace_editor": "Edit",
    "run_shell_command": "Bash",
    "task": "Task",
}


def _empty(event: str) -> HookEnvelope:
    return HookEnvelope(
        event=event,
        host=_HOST,
        tool_name=None,
        tool_input=MappingProxyType({}),
        cwd=str(_host_env.project_dir()),
        session_id=_host_env.session_id(),
        raw=MappingProxyType({}),
    )


def _normalise_tool(name):
    if not isinstance(name, str):
        return None
    return GEMINI_TO_CANONICAL.get(name, name)


def parse_stdin(event: str) -> HookEnvelope:
    try:
        raw_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return _empty(event)
    return parse_payload(event, raw_text)


def parse_payload(event: str, raw_text: str) -> HookEnvelope:
    if not raw_text:
        return _empty(event)
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError):
        return _empty(event)
    if not isinstance(data, dict):
        return _empty(event)

    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    file_path = None
    if isinstance(tool_input, dict):
        # Gemini may use file_path (write_file) or path (edit_file); both supported.
        fp = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(fp, str) and fp.strip():
            file_path = fp

    return HookEnvelope(
        event=event,
        host=_HOST,
        tool_name=_normalise_tool(data.get("tool_name")),
        tool_input=MappingProxyType(dict(tool_input)),
        file_path=file_path,
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else str(_host_env.project_dir()),
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else _host_env.session_id(),
        transcript_path=data.get("transcript_path") if isinstance(data.get("transcript_path"), str) else None,
        prompt=data.get("prompt") if isinstance(data.get("prompt"), str) else None,
        raw=MappingProxyType(dict(data)),
    )


__all__ = ["parse_stdin", "parse_payload", "GEMINI_TO_CANONICAL"]
