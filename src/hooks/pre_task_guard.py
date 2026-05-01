#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the ``Task`` tool (subagent spawn).

Wired by ``.claude/settings.json`` with matcher ``Task``. The hook is a
"belt and braces" layer over the Edit/Write/Bash guards: a Task subagent
inherits the parent's settings.json when run inside ``CLAUDE_PROJECT_DIR``,
but its prompt may still be steered to mutate guard files via the parent's
own tools. We screen the prompt for mutation verbs combined with mentions
of guarded paths and reject before the subagent boots.

Exit codes:
  0 — task allowed (read-only research, etc.). Audit log still records it.
  2 — task blocked because the prompt asked for code-mutation against a
      guarded path AND ``RC_ALLOW_GUARD_EDIT != 1``. Bypass via
      ``RC_ALLOW_SUBAGENT_GUARD_EDIT=1`` to downgrade to a warning.
  0 — also returned on malformed stdin (never block on bad payload).

Audit: every Task spawn is recorded regardless of decision.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# Local import bootstrap.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402

TOOL_NAME = "Task"

# Path fragments shared with pre_edit_guard.GUARDED_PATHS / pre_bash_guard.
GUARDED_PATH_FRAGMENTS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    "src/hooks/pre_edit_guard.py",
    "src/hooks/pre_bash_guard.py",
    "src/hooks/post_bash_revive.py",
    "src/hooks/pre_plan_guard.py",
    "src/hooks/pre_task_guard.py",
    "src/hooks/audit_log.py",
    "src/s2_core.py",
    "src/grammars.py",
    "src/ssm_backbone.py",
    "src/mcp_reasoner.py",
    "scripts/start-sidecar.sh",
)

# Mutation verbs — word-boundary, case-insensitive.
_MUTATION_VERB_RE = re.compile(
    r"\b(?:implement|edit|edits?|writ(?:e|es|ing)|modif(?:y|ies|ying)|"
    r"refactor(?:ing|s)?|creat(?:e|es|ing)|delet(?:e|es|ing)|patch(?:es|ing)?)\b",
    re.IGNORECASE,
)

# Explicit override env vars.
_HARD_OVERRIDE = "RC_ALLOW_GUARD_EDIT"
_SOFT_OVERRIDE = "RC_ALLOW_SUBAGENT_GUARD_EDIT"


def _read_payload() -> Optional[Dict[str, Any]]:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _exit(code: int, *messages: str) -> None:
    for msg in messages:
        if msg:
            sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def _detect_mutation_verbs(prompt: str) -> List[str]:
    found: List[str] = []
    for m in _MUTATION_VERB_RE.finditer(prompt or ""):
        token = m.group(0).lower()
        if token not in found:
            found.append(token)
    return found


def _detect_guarded_paths(prompt: str) -> List[str]:
    if not prompt:
        return []
    out: List[str] = []
    for frag in GUARDED_PATH_FRAGMENTS:
        if frag in prompt and frag not in out:
            out.append(frag)
    return out


def screen_prompt(prompt: str) -> Dict[str, Any]:
    """Return a screening result dict.

    Keys: verbs, guarded_paths, decision, reason.
    decision in {'allowed', 'advisory', 'blocked'}.
    """
    verbs = _detect_mutation_verbs(prompt)
    paths = _detect_guarded_paths(prompt)

    if not verbs:
        return {"verbs": [], "guarded_paths": paths, "decision": "allowed", "reason": "no_mutation_verb"}

    if paths:
        # Strong override — same env as the Edit/Bash guard.
        if os.environ.get(_HARD_OVERRIDE) == "1":
            return {
                "verbs": verbs,
                "guarded_paths": paths,
                "decision": "advisory",
                "reason": f"override_{_HARD_OVERRIDE}",
            }
        # Soft override — downgrade to warning.
        if os.environ.get(_SOFT_OVERRIDE) == "1":
            return {
                "verbs": verbs,
                "guarded_paths": paths,
                "decision": "advisory",
                "reason": f"override_{_SOFT_OVERRIDE}",
            }
        return {
            "verbs": verbs,
            "guarded_paths": paths,
            "decision": "blocked",
            "reason": "mutation_verb_on_guarded_path",
        }

    return {
        "verbs": verbs,
        "guarded_paths": [],
        "decision": "advisory",
        "reason": "mutation_verb_outside_guarded_paths",
    }


def main() -> None:
    started = time.time()
    payload = _read_payload()
    if payload is None:
        # Record what little we know; never block on bad stdin.
        audit_log.append_event(audit_log.new_event(
            tool_name=TOOL_NAME,
            decision="allowed",
            reason="malformed_payload",
            latency_ms=int((time.time() - started) * 1000),
        ))
        _exit(0, "[hybrid-reasoner] task-guard: malformed payload, allowing.")
        return

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool_name != "Task" or not isinstance(tool_input, dict):
        _exit(0)
        return

    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        prompt = ""
    subagent_type = tool_input.get("subagent_type") or tool_input.get("description") or ""

    result = screen_prompt(prompt)
    decision = result["decision"]
    verbs = result["verbs"]
    guarded = result["guarded_paths"]
    reason = result["reason"]

    latency_ms = int((time.time() - started) * 1000)

    audit_log.append_event(audit_log.new_event(
        tool_name=TOOL_NAME,
        decision=decision,
        latency_ms=latency_ms,
        subagent_type=str(subagent_type),
        mutation_verbs=verbs,
        guarded_paths=guarded,
        reason=reason,
        prompt_bytes=len(prompt.encode("utf-8", errors="replace")),
    ))

    if decision == "blocked":
        _exit(
            2,
            "[hybrid-reasoner] BLOCKED: subagent prompt targets guard files.\n"
            f"  verbs: {verbs}\n"
            f"  guarded paths in prompt: {guarded}\n"
            f"  override: set {_SOFT_OVERRIDE}=1 (warn-only) or {_HARD_OVERRIDE}=1 (full bypass).",
        )
        return

    if decision == "advisory" and (verbs or guarded):
        _exit(
            0,
            "[hybrid-reasoner] task-guard advisory: "
            f"verbs={verbs} guarded_paths={guarded} reason={reason}",
        )
        return

    _exit(0)


if __name__ == "__main__":
    main()
