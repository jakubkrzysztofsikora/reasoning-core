"""Decide what to do with confirmed duplicate hits.

Pure over ``DupHit`` lists + the set of files in the working diff, so the
per-edit and Stop hooks share one rule set, and it is testable without a model.
"""
from __future__ import annotations

import os
import re
import sys

from pathlib import Path
from typing import Any, Callable

from . import findings as ledger
from .index import extract_functions, logic_tokens
from ..grammars import UnsupportedLanguageError
from .repo_index import _DUP_SOURCE_EXTENSIONS
from .oracle import CONFIRM_DEFAULT, RECALL_DEFAULT, DupHit, find_near_duplicates

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
    for directory, subdirectories, files in os.walk(repo_root):
        # Pruning subdirectories in place stops os.walk descending into them.
        subdirectories[:] = sorted(
            d for d in subdirectories if d not in _SKIP_DIRS and not d.startswith(".")
        )
        for file_name in sorted(files):
            full_path = os.path.join(directory, file_name)
            rel = os.path.relpath(full_path, repo_root).replace(os.sep, "/")
            if not is_test_file(rel):
                continue
            try:
                with open(full_path, encoding="utf-8", errors="replace") as fh:
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


def _match(h: DupHit) -> dict[str, Any]:
    return {
        "path": h.path, "name": h.name, "lineno": h.lineno, "logic": round(h.logic_ratio, 3),
        # The rare tokens both copies share, without their kind prefix ("N:"):
        # the evidence shown next to the score.
        "shared": [t.split(":", 1)[-1] for t in h.shared[:4]],
    }


def format_match(m: dict[str, Any]) -> str:
    """One match line, shared by the per-edit and Stop messages."""
    line = f"    - {m['name']}  {m['path']}:{m['lineno']}  (logic {m['logic']:.2f})"
    if m.get("shared"):
        line += f"  [shares {', '.join(m['shared'])}]"
    return line


def _format_inline(name: str, rel: str, finding: dict[str, Any]) -> str:
    lines = [f"  `{name}` ({rel}) duplicates:"]
    lines += [format_match(m) for m in finding["matches"]]
    cover = "test-covered" if finding["test_covered"] else "not test-covered"
    lines.append(f"  {finding['sites']} sites, {cover} (a test names the function or its file).")
    if finding["verdict"] == EXTRACT:
        lines.append(
            f"  RULE: >= {EXTRACT_MIN_SITES} sites + non-trivial + test-covered -> EXTRACT "
            "a shared function now. The session will not end while this is open."
        )
    else:
        lines.append("  Reuse one copy while you are in this file; recorded for the session-end review.")
    return "\n".join(lines)


def check_edit(
    file_path: str,
    source: str,
    index: Any,
    *,
    embed_fn: Callable[[str], Any],
    repo_root: str,
    changed: set[str] | None,
    ledger_file: Path,
    test_texts: dict[str, str] | None = None,
    recall: float = RECALL_DEFAULT,
    confirm: float = CONFIRM_DEFAULT,
    top_k: int = 3,
) -> str | None:
    """Check every function in the edited file, record each duplicate in the
    ledger, and return the inline message (or ``None``).

    Routing: a match in another file of the working diff is a likely move
    (recorded, silent); matches only in untouched files are a tracked follow-up
    (recorded, silent, shown at Stop); a match in the edited file itself is
    shown inline, once per finding. Ledger write failures raise ``LedgerError``.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _DUP_SOURCE_EXTENSIONS:
        # Parseable is not enough: only these languages are indexed, so any
        # other file would wrongly read as "checked, no duplicates".
        raise UnsupportedLanguageError(ext)
    if not source or index is None or len(index) == 0:
        return None
    rel = os.path.relpath(file_path, repo_root).replace(os.sep, "/")
    other_changed = None if changed is None else changed - {rel}
    seen = set(ledger.load(ledger_file))
    # The index refresh after the edit already embedded this file; reuse those
    # vectors and only embed functions the index lacks (stale index, size cap).
    indexed = {
        (r.name, r.lineno): i for i, r in enumerate(index.records) if r.path == rel
    }
    blocks = []
    for name, line, func_source in extract_functions(file_path, source):
        tokens = logic_tokens(file_path, func_source)
        if not tokens:
            continue
        row = indexed.get((name, line))
        vec = index.embeddings[row] if row is not None else embed_fn(func_source)
        hits = find_near_duplicates(
            vec, tokens, index.records, index.embeddings, index.token_df,
            recall=recall, confirm=confirm,
            skip=lambda i, rec, nm=name: rec.path == rel and rec.name == nm,
        )
        if not hits:
            continue
        real, moves = split_moves(hits, other_changed)
        if real:
            if test_texts is None:
                test_texts = load_test_texts(repo_root)
            covered = all(site_is_covered(p, n, test_texts) for p, n in
                          [(rel, name)] + [(h.path, h.name) for h in real])
            finding = ledger.make_finding(
                added_path=rel, added_name=name, matches=[_match(h) for h in real],
                verdict=verdict(sites=1 + len(real), n_tokens=len(tokens), covered=covered),
                in_diff=any(h.path == rel for h in real), likely_move=False,
                test_covered=covered,
            )
        else:
            finding = ledger.make_finding(
                added_path=rel, added_name=name, matches=[_match(h) for h in moves],
                verdict=NOTE, in_diff=False, likely_move=True, test_covered=False,
            )
        ledger.record(ledger_file, finding)
        if finding["in_diff"] and finding["id"] not in seen:
            finding["matches"] = finding["matches"][:top_k]
            blocks.append(_format_inline(name, rel, finding))
    if not blocks:
        return None
    return (
        "[reasoning-core] DUPLICATE CHECK -- code in the file you just edited is duplicated:\n\n"
        + "\n\n".join(blocks)
    )
