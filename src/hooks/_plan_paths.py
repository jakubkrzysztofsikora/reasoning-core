"""Plan-vocabulary path extraction shared between pre_plan_guard and the
plan-grounding gate.

Single source of truth for "what file paths does this PLAN.md mention?".
Two extraction strategies merged:

  1. Bulleted items with LOC annotation, e.g. ``- src/foo.py — ~120 LOC``.
  2. Bare backticked paths, e.g. `` `src/bar.ts` ``.

Consumers:
  - ``pre_plan_guard._count_distinct_file_paths`` — wired (existing semantics preserved).
  - ``_dispatch.gate_plan_grounding`` — Phase 3, not yet wired in this commit.

Self-contained — no imports from other hook modules — to avoid cycles.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Match bulleted lines like "- src/foo.py — ~120 LOC" or "* `src/foo.py`: ~80 lines".
# Mirrors pre_plan_guard._FILE_LINE_RE exactly to preserve semantics.
_FILE_LINE_RE = re.compile(
    r"""
    [\-\*\+]\s*                          # bullet
    `?(?P<path>[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`?
    .*?                                  # any text in between
    (?:~?\s*(?P<loc>\d{2,5})\s*(?:LOC|lines?|locs?))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_BARE_PATH_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})`")


def extract_files_with_loc(content: str) -> List[Tuple[str, int]]:
    """Find file_path + estimated LOC pairs in plan body (bulleted lines)."""
    out: List[Tuple[str, int]] = []
    for m in _FILE_LINE_RE.finditer(content):
        try:
            loc = int(m.group("loc"))
        except (TypeError, ValueError):
            continue
        path = m.group("path") or ""
        if path:
            out.append((path, loc))
    return out


def distinct_file_paths(content: str) -> set[str]:
    """Return the union of bulleted-with-LOC and bare-backticked file paths."""
    paths: set[str] = {p for p, _ in extract_files_with_loc(content)}
    paths.update(_BARE_PATH_RE.findall(content))
    return paths
