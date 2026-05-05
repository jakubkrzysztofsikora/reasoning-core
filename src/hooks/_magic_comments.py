"""In-file directives that bypass reasoning-core checks.

Scans the first 20 lines of file content for one of:
  # rc:skip                 — bypass all checks
  # rc:skip-lang            — bypass language fingerprint lock only
  # rc:skip-mock            — bypass mock-detector only
  # rc:skip-quality         — bypass plan-quality CGS only
  # rc:override <reason>    — allow but log the override + reason

JS/TS/C# `//` and HTML `<!-- -->` comment forms are accepted too.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional


_DIRECTIVE_RE = re.compile(
    r"(?:#|//|<!--)\s*rc:(skip-lang|skip-mock|skip-quality|override|skip)\b[ \t]*([^\n]*?)(?:\s*-->)?\s*$",
    re.MULTILINE,
)
_SCAN_LINES = 20


class Directive(NamedTuple):
    name: str           # one of: skip, skip-lang, skip-mock, skip-quality, override
    reason: str         # only meaningful for "override"
    line: int           # 1-indexed line number


def parse(content: Optional[str]) -> Optional[Directive]:
    if not content:
        return None
    head = "\n".join(content.splitlines()[:_SCAN_LINES])
    match = _DIRECTIVE_RE.search(head)
    if not match:
        return None
    name = match.group(1).strip()
    reason = (match.group(2) or "").strip()
    line = head[: match.start()].count("\n") + 1
    return Directive(name=name, reason=reason, line=line)


def bypasses_all(d: Optional[Directive]) -> bool:
    return d is not None and d.name in ("skip", "override")


def bypasses(d: Optional[Directive], domain: str) -> bool:
    """domain in {"lang", "mock", "quality"}."""
    if d is None:
        return False
    if d.name in ("skip", "override"):
        return True
    return d.name == f"skip-{domain}"
