"""Host-agnostic hook envelope.

Adapters in :mod:`src.hooks.adapters` parse host-specific stdin payloads
(Claude Code, Gemini CLI, Copilot CLI, Vibe CLI) into a single
``HookEnvelope`` shape consumed by the dispatch chain.

The dataclass is frozen and ``raw``/``tool_input`` are wrapped in
``MappingProxyType`` so the envelope is immutable AT THE TOP LEVEL only.
**Caveat (Phase 1a review)**: nested objects (e.g. ``raw["tool_input"]``,
``tool_input["edits"][0]``) remain the original mutable Python dicts.
Gates that need to compare/hash deep state should ``copy.deepcopy`` the
field themselves — we deliberately don't deep-freeze because that would
force adapters to copy multi-MB Edit payloads on every hook invocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Optional


@dataclass(frozen=True)
class HookEnvelope:
    event: str                              # PreToolUse, PostToolUse, SessionStart, ...
    host: str                               # claude | gemini | copilot | vibe | unknown
    tool_name: Optional[str] = None
    tool_input: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    file_path: Optional[str] = None
    before_src: Optional[str] = None
    after_src: Optional[str] = None
    cwd: Optional[str] = None
    session_id: Optional[str] = None
    transcript_path: Optional[str] = None
    prompt: Optional[str] = None
    # Escape hatch: read fields not yet enumerated (permission_mode,
    # hook_event_name, etc.) via ``envelope.raw["x"]``.
    raw: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


__all__ = ["HookEnvelope"]
