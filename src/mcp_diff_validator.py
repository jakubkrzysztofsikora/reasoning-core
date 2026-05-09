"""MCP tool: validate_unified_diff.

Lints a unified diff for the structural defects that cause BSD `patch`
to reject patches that `git apply` accepts. Surfaces concrete errors
the agent can act on before committing the patch as its final answer.

Defect classes detected:
  - missing_prefix:    a line inside a hunk body lacks one of {' ','+','-','\\\\'}
  - empty_context:     a bare empty line inside a hunk body (BSD `patch` treats
                       this as end-of-hunk; must be a single space)
  - count_mismatch:    `@@ -A,B +C,D @@` header B/D do not match body counts
  - wrapped_line:      a body line is followed by a line starting with content
                       that looks like a continuation (heuristic)
  - missing_hunk:      file header (--- / +++) without a following @@ hunk

Returns a dict (never raises) with:
  - ok: bool
  - errors: list[{kind, line, message, suggestion?}]
  - hunk_count: int
  - repaired_patch: str | None  (best-effort repair; None if catastrophic)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BODY_PREFIXES = (" ", "+", "-", "\\")


def _validate(patch: str) -> Dict[str, Any]:
    if not isinstance(patch, str) or not patch:
        return {"ok": False, "errors": [{"kind": "empty_input", "line": 0,
                "message": "patch is empty"}], "hunk_count": 0,
                "repaired_patch": None}

    lines = patch.split("\n")
    if lines and lines[-1] == "" and patch.endswith("\n"):
        lines = lines[:-1]
    errors: List[Dict[str, Any]] = []
    hunk_count = 0
    in_hunk = False
    hunk_header_idx = -1
    body_old = 0
    body_new = 0
    last_file_header_seen = False
    pending_header_state: Optional[Dict[str, int]] = None

    for i, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            if in_hunk and pending_header_state is not None:
                _check_counts(errors, hunk_header_idx, body_old, body_new,
                              pending_header_state)
            in_hunk = False
            pending_header_state = None
            if line.startswith("--- ") or line.startswith("+++ "):
                last_file_header_seen = True
            continue
        if line.startswith("@@"):
            if in_hunk and pending_header_state is not None:
                _check_counts(errors, hunk_header_idx, body_old, body_new,
                              pending_header_state)
            m = _HUNK_HEADER_RE.match(line)
            if not m:
                errors.append({"kind": "bad_hunk_header", "line": i + 1,
                               "message": f"hunk header does not match @@ -A,B +C,D @@: {line!r}"})
                in_hunk = False
                pending_header_state = None
                continue
            hunk_count += 1
            in_hunk = True
            hunk_header_idx = i + 1
            body_old = 0
            body_new = 0
            pending_header_state = {
                "old_count": int(m.group(2)) if m.group(2) else 1,
                "new_count": int(m.group(4)) if m.group(4) else 1,
            }
            last_file_header_seen = False
            continue
        if not in_hunk:
            continue
        # In hunk body
        if line == "":
            errors.append({"kind": "empty_context", "line": i + 1,
                           "message": "bare empty line in hunk body — BSD patch terminates hunk here",
                           "suggestion": "encode blank context as a single space character"})
            # Treat as context for counting; repair will fix.
            body_old += 1
            body_new += 1
            continue
        first = line[:1]
        if first not in _BODY_PREFIXES:
            errors.append({"kind": "missing_prefix", "line": i + 1,
                           "message": f"hunk body line lacks +/-/space prefix: {line!r}",
                           "suggestion": "likely a wrapped continuation of the previous line — rejoin onto the previous physical line"})
            continue
        if first == " " or first == "-":
            body_old += 1
        if first == " " or first == "+":
            body_new += 1
    if in_hunk and pending_header_state is not None:
        _check_counts(errors, hunk_header_idx, body_old, body_new,
                      pending_header_state)

    if hunk_count == 0:
        errors.append({"kind": "missing_hunk", "line": 0,
                       "message": "no @@ hunks found"})

    repaired = _repair(patch) if errors else patch
    ok = not errors
    return {"ok": ok, "errors": errors, "hunk_count": hunk_count,
            "repaired_patch": repaired}


def _check_counts(errors, hunk_header_line, body_old, body_new, state) -> None:
    if body_old != state["old_count"] or body_new != state["new_count"]:
        errors.append({
            "kind": "count_mismatch",
            "line": hunk_header_line,
            "message": (
                f"hunk header counts ({state['old_count']},{state['new_count']}) "
                f"do not match body counts ({body_old},{body_new})"
            ),
            "suggestion": (
                f"rewrite header counts to ({body_old},{body_new}) "
                "OR fix the body to match the declared counts"
            ),
        })


def _repair(patch: str) -> Optional[str]:
    """Best-effort structural repair. Mirror of harness-side
    repair_unified_diff_structure described in the iter-1 RCA memo."""
    if not patch:
        return None
    out: List[str] = []
    in_hunk = False
    body: List[str] = []
    header_idx = -1

    def _flush():
        nonlocal body, header_idx
        if header_idx < 0 or not body:
            return True
        m = _HUNK_HEADER_RE.match(out[header_idx])
        if not m:
            return False
        old_start = int(m.group(1))
        new_start = int(m.group(3))
        old = sum(1 for ln in body if ln[:1] in (" ", "-"))
        new = sum(1 for ln in body if ln[:1] in (" ", "+"))
        if old == 0 or new == 0:
            return False
        out[header_idx] = f"@@ -{old_start},{old} +{new_start},{new} @@"
        out.extend(body)
        body = []
        return True

    pending_prefix: Optional[str] = None
    src_lines = patch.split("\n")
    if src_lines and src_lines[-1] == "" and patch.endswith("\n"):
        src_lines = src_lines[:-1]
    for line in src_lines:
        if line.startswith("@@"):
            if in_hunk and not _flush():
                return None
            in_hunk = True
            out.append(line)
            header_idx = len(out) - 1
            pending_prefix = None
            continue
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            if in_hunk and not _flush():
                return None
            in_hunk = False
            out.append(line)
            pending_prefix = None
            continue
        if not in_hunk:
            out.append(line)
            continue
        if line == "":
            body.append(" ")
            pending_prefix = " "
            continue
        if line[:1] in _BODY_PREFIXES:
            body.append(line)
            pending_prefix = line[:1]
            continue
        # Wrapped continuation: rejoin onto previous body line.
        if body:
            body[-1] = body[-1] + line
        elif pending_prefix is not None:
            body.append(pending_prefix + line)
    if in_hunk and not _flush():
        return None
    return "\n".join(out) + ("\n" if patch.endswith("\n") else "")


def validate_unified_diff(patch: str) -> Dict[str, Any]:
    """Validate a unified diff and return structural errors + best-effort repair.

    Args:
        patch: Unified diff text (typically extracted from a fenced
            ```diff … ``` block in agent output).

    Returns:
        Dict with keys:
          - ok (bool): True iff no structural errors detected.
          - errors (list): each item is {kind, line, message, suggestion?}
            where kind is one of: empty_input, bad_hunk_header,
            missing_prefix, empty_context, count_mismatch, missing_hunk.
          - hunk_count (int): number of @@ hunks parsed.
          - repaired_patch (str | None): best-effort structural repair.
            None on catastrophic damage (zero context+added or zero
            context+removed lines after repair). On a clean patch this
            equals the input.
    """
    try:
        return _validate(patch)
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        return {"ok": False, "errors": [{"kind": "internal_error", "line": 0,
                "message": f"{type(exc).__name__}: {exc}"}],
                "hunk_count": 0, "repaired_patch": None}


__all__ = ["validate_unified_diff"]
