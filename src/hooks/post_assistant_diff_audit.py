#!/usr/bin/env python3
"""Stop / UserPromptSubmit hook: audit assistant-emitted unified diffs.

Reads transcript_path from the hook payload, finds the most recent
assistant text that contains a fenced ```diff … ``` (or bare unified
diff) block, runs the structural validator, and — if errors are
detected — emits an additionalContext blurb that:

  1. Names each defect (kind, line, suggestion).
  2. Includes the best-effort repaired patch as a reference.
  3. Tells the agent to re-emit the patch in the next turn.

Fires under RC_DIFF_AUDIT=1 (default off — opt-in to keep treatment
integrity until prereg amendment lands). Hook never blocks; only
injects guidance.

Wire as Stop hook in Gemini/Claude settings.json (preferred — fires
right after the model finishes); UserPromptSubmit also works as a
fallback for hosts that don't surface Stop. SWE-bench's single-prompt
flow won't get a second turn, but the hook still emits its findings to
stderr where the harness can ingest them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from src.mcp_diff_validator import validate_unified_diff
except Exception:  # noqa: BLE001 - hook must never crash
    validate_unified_diff = None  # type: ignore

_FENCED_DIFF_RE = re.compile(
    r"```(?:diff|patch)?\s*\n(diff --git .+?)\n```",
    re.DOTALL,
)
_BARE_DIFF_RE = re.compile(r"(^|\n)(diff --git .+?)(?=\n(?:diff --git |\Z))", re.DOTALL)


def _read_payload() -> Dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_last_assistant_diff(transcript_path: str) -> Optional[str]:
    """Walk transcript.jsonl from end; return the diff text from the most
    recent assistant message that contains one. None if none found."""
    p = Path(transcript_path)
    if not p.is_file():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("role") != "assistant" and ev.get("type") != "message":
            continue
        text = ev.get("content") or ev.get("text") or ""
        if not isinstance(text, str) or "diff --git " not in text:
            continue
        m = _FENCED_DIFF_RE.search(text)
        if m:
            return m.group(1)
        m = _BARE_DIFF_RE.search(text)
        if m:
            return m.group(2)
    return None


def _format_advisory(result: Dict[str, Any]) -> str:
    errs = result.get("errors") or []
    repaired = result.get("repaired_patch")
    bullets = []
    for e in errs[:6]:
        kind = e.get("kind", "?")
        ln = e.get("line", "?")
        msg = e.get("message", "")
        sug = e.get("suggestion")
        line = f"  - [{kind}] line {ln}: {msg}"
        if sug:
            line += f"\n    suggestion: {sug}"
        bullets.append(line)
    body = (
        "[hybrid-reasoner] DIFF AUDIT: your last unified diff has structural "
        "errors that BSD `patch` will reject (even if `git apply` accepts it).\n"
        + "\n".join(bullets)
        + "\n\nRe-emit the patch in your next turn. Required format:\n"
        "  - every hunk-body line starts with one of: ' ', '+', '-', '\\\\'\n"
        "  - no wrapped lines; each body line stays on ONE physical line\n"
        "  - @@ -A,B +C,D @@ counts MUST match body counts\n"
        "  - blank context lines encoded as a single space\n"
    )
    if repaired:
        body += "\nReference repaired form (verify before emitting):\n```diff\n"
        body += repaired
        body += "\n```\n"
    return body


def _emit_additional_context(text: str, event_name: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(out))


def main() -> None:
    if os.environ.get("RC_DIFF_AUDIT") != "1":
        sys.exit(0)
    if validate_unified_diff is None:
        sys.exit(0)
    payload = _read_payload()
    event_name = (
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or "Stop"
    )
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        sys.exit(0)
    diff_text = _find_last_assistant_diff(transcript_path)
    if not diff_text:
        sys.exit(0)
    result = validate_unified_diff(diff_text)
    if result.get("ok"):
        sys.exit(0)
    advisory = _format_advisory(result)
    sys.stderr.write(advisory + "\n")
    _emit_additional_context(advisory, event_name)
    sys.exit(0)


if __name__ == "__main__":
    main()
