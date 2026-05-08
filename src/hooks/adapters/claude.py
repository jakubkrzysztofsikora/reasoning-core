"""Claude Code stdin -> HookEnvelope adapter.

Claude Code passes hook payloads on stdin as JSON. This adapter parses
and normalises them into the host-agnostic :class:`HookEnvelope`.

Parse-failure contract (review-driven, Phase 1a):
- NEVER raises.
- On malformed JSON / non-dict / unknown event, returns a HookEnvelope
  with ``tool_name=None`` and ``raw={}``. The orchestrator decides what to
  do next (typically: audit ``decision="malformed_payload"`` and exit 0).
- ``cwd`` defaults to ``_host_env.project_dir()`` (NOT ``os.getcwd()``)
  because Claude commonly launches hooks from subdirs.
"""

from __future__ import annotations

import json
import sys
from types import MappingProxyType
from typing import Mapping, Optional

from src.hooks import _host_env
from src.hooks._envelope import HookEnvelope

_HOST = "claude"


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
    """Read Claude Code stdin and return a :class:`HookEnvelope`."""
    try:
        raw_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return _empty(event)
    return parse_payload(event, raw_text)


def parse_payload(event: str, raw_text: str) -> HookEnvelope:
    """Pure variant for tests: takes the raw JSON string directly."""
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

    return HookEnvelope(
        event=event,
        host=_HOST,
        tool_name=data.get("tool_name") if isinstance(data.get("tool_name"), str) else None,
        tool_input=MappingProxyType(dict(tool_input)),
        file_path=file_path,
        cwd=data.get("cwd") if isinstance(data.get("cwd"), str) else str(_host_env.project_dir()),
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else _host_env.session_id(),
        transcript_path=data.get("transcript_path") if isinstance(data.get("transcript_path"), str) else None,
        prompt=data.get("prompt") if isinstance(data.get("prompt"), str) else None,
        raw=MappingProxyType(dict(data)),
    )


__all__ = ["parse_stdin", "parse_payload"]
