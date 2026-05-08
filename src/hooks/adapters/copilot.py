"""GitHub Copilot CLI stdin -> HookEnvelope adapter.

Verification (2026-05-08, GitHub Copilot CLI v1.0.29): the standalone
``copilot`` binary has NO hook subcommand. Phase 3 collapses to MCP +
skill + instructions only — runtime gating happens via the ``gate_edit``
MCP tool (Phase 4).

This adapter is retained as a thin shim for forward compatibility: if
GitHub adds a hook surface to the Copilot CLI, the adapter is already
in place and Phase 3 can re-expand without code churn elsewhere.
"""

from __future__ import annotations

import json
import sys
from types import MappingProxyType

from src.hooks import _host_env
from src.hooks._envelope import HookEnvelope

_HOST = "copilot"

COPILOT_TO_CANONICAL = {
    "write": "Write",
    "edit": "Edit",
    "str_replace_editor": "Edit",
    "bash": "Bash",
    "run": "Bash",
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
        fp = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(fp, str) and fp.strip():
            file_path = fp

    raw_name = data.get("tool_name")
    canonical = COPILOT_TO_CANONICAL.get(raw_name, raw_name) if isinstance(raw_name, str) else None

    return HookEnvelope(
        event=event,
        host=_HOST,
        tool_name=canonical,
        tool_input=MappingProxyType(dict(tool_input)),
        file_path=file_path,
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else str(_host_env.project_dir()),
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else _host_env.session_id(),
        raw=MappingProxyType(dict(data)),
    )


def emit_block_decision(message: str) -> None:
    """If Copilot ever surfaces a hook envelope, emit ``permissionDecision: deny``
    JSON on stdout (the contract documented in early research). Currently a
    no-op call site since copilot has no hooks; retained for forward compat.
    """
    out = {
        "permissionDecision": "deny",
        "permissionDecisionReason": message,
    }
    sys.stdout.write(json.dumps(out))


__all__ = ["parse_stdin", "parse_payload", "COPILOT_TO_CANONICAL", "emit_block_decision"]
