"""Decide what to do with confirmed duplicate hits.

Pure over ``DupHit`` lists + the set of files in the working diff, so the
per-edit and Stop hooks share one rule set and it is testable without a model.
"""
from __future__ import annotations

import os
import re
import sys

from .oracle import DupHit

EXTRACT = "extract"
NOTE = "note"

# The EXTRACT rule: enough copies, enough logic, and tests to catch a slip.
EXTRACT_MIN_SITES = 3
# Measured on the motivating cases: click-outside + Escape handler = 23 logic
# tokens; trivial helpers (replaceState wrapper, render-with-provider) = 4-8.
MIN_LOGIC_TOKENS = 12

_TEST_FILE_RE = re.compile(
    r"(^|/)(tests?|__tests__)/|\.(test|spec)\.[^/]+$|(^|/)test_[^/]+$|_test\.[^/]+$"
)
_SKIP_DIRS = frozenset({"node_modules", "dist", "build", "vendor"})


def split_moves(
    hits: list[DupHit], changed: set[str] | None
) -> tuple[list[DupHit], list[DupHit]]:
    """Split hits into (real, likely_moves).

    A hit whose file is in the working diff is a likely relocation: the agent is
    editing the file the copy came from, so the copy is probably on its way out.
    The Stop hook re-checks and drops it once the source copy is gone.

    ``changed is None`` means git could not say what changed; every hit is then
    reported as real rather than hidden.
    """
    if changed is None:
        return list(hits), []
    real = [h for h in hits if h.path not in changed]
    moves = [h for h in hits if h.path in changed]
    return real, moves


def is_test_file(rel_path: str) -> bool:
    return bool(_TEST_FILE_RE.search(rel_path.replace(os.sep, "/")))


def load_test_texts(repo_root: str) -> dict[str, str]:
    """Repo-relative path -> text, for every test file outside vendored and
    hidden directories. An unreadable test file is reported on stderr and
    skipped: one bad file must not hide coverage for the rest."""
    texts: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace(os.sep, "/")
            if not is_test_file(rel):
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as fh:
                    texts[rel] = fh.read()
            except OSError as exc:
                print(f"dup-oracle: cannot read test file {rel}: {exc}", file=sys.stderr)
    return texts


def site_is_covered(site_path: str, name: str, test_texts: dict[str, str]) -> bool:
    """Heuristic: a site is test-covered when it lives in a test file, or a test
    mentions the function's name or its file's stem (tests usually import the
    component, not a helper defined inside it)."""
    if is_test_file(site_path):
        return True
    stem = os.path.splitext(os.path.basename(site_path))[0]
    stem_re = re.compile(rf"\b{re.escape(stem)}\b")
    name_re = re.compile(rf"\b{re.escape(name)}\b")
    return any(name_re.search(t) or stem_re.search(t) for t in test_texts.values())


def verdict(*, sites: int, n_tokens: int, covered: bool) -> str:
    """EXTRACT when there are enough copies, enough logic and test cover to
    make the change safe; otherwise a note."""
    if sites >= EXTRACT_MIN_SITES and n_tokens >= MIN_LOGIC_TOKENS and covered:
        return EXTRACT
    return NOTE
