"""OpenAI Codex CLI stdin -> HookEnvelope adapter.

Codex CLI (openai/codex) ships PreToolUse hooks with the same JSON
envelope shape as Claude Code. This adapter reuses the Claude parser
with host-tag overrides.
"""

from __future__ import annotations

import json
import sys
from types import MappingProxyType

from src.hooks import _host_env
from src.hooks._envelope import HookEnvelope

_HOST = "codex"


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

    return HookEnvelope(
        event=event,
        host=_HOST,
        tool_name=data.get("tool_name") if isinstance(data.get("tool_name"), str) else None,
        tool_input=MappingProxyType(dict(data.get("tool_input") or {})),
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else str(_host_env.project_dir()),
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else _host_env.session_id(),
        raw=MappingProxyType(dict(data)),
    )


__all__ = ["parse_stdin", "parse_payload"]
