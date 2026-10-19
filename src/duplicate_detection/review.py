"""Session-end review of the findings ledger, for the Stop hook.

Re-checks every open finding against the files as they are now, using only
tree-sitter + the logic-token ratio (no model), then decides whether the stop
should be blocked. Deterministic by design: the block it feeds is a hard
enforcement signal (AGENTS.md).
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..grammars import UnsupportedLanguageError
from . import findings as ledger
from .check import EXTRACT, MIN_LOGIC_TOKENS, format_match, load_test_texts, site_is_covered, verdict
from .index import extract_functions, logic_ratio_tokens, logic_tokens
from .oracle import CONFIRM_DEFAULT

# Blocks per EXTRACT finding before the stop is let through and escalated.
MAX_BLOCKS = 3


@dataclass
class StopDecision:
    block: bool
    message: str = ""
    extract: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)


class _Unverifiable(Exception):
    """A file could not be read; keep the finding as recorded."""


def _function(repo_root: str, rel: str, name: str) -> Optional[tuple[int, list[str]]]:
    """(line, logic tokens) of ``name`` in ``rel`` now, or None if it is gone."""
    path = os.path.join(repo_root, rel)
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _Unverifiable(f"{rel}: {exc}") from exc
    for func_name, line, func_source in extract_functions(path, src):
        if func_name == name:
            return line, logic_tokens(path, func_source)
    return None


def revalidate(
    row: dict[str, Any], repo_root: str, test_texts: dict[str, str], *, confirm: float = CONFIRM_DEFAULT
) -> Optional[dict[str, Any]]:
    """The finding as it stands now, or None when it no longer exists (the
    added copy or every matched copy is gone or no longer equivalent, or an
    EXTRACT finding's copies have shrunk to thin wrappers around a shared
    function)."""
    added = _function(repo_root, row["added_path"], row["added_name"])
    if added is None:
        return None
    _, tokens = added
    if (row["verdict"] == EXTRACT or row.get("was_extract")) and len(tokens) < MIN_LOGIC_TOKENS:
        # The logic left the copy: what remains delegates, so it was extracted.
        return None
    kept = []
    for m in row["matches"]:
        now = _function(repo_root, m["path"], m["name"])
        if now is None:
            continue
        ratio = logic_ratio_tokens(tokens, now[1])
        if ratio >= confirm:
            kept.append(dict(m, lineno=now[0], logic=round(ratio, 3)))
    if not kept:
        return None
    sites = [(row["added_path"], row["added_name"])] + [(m["path"], m["name"]) for m in kept]
    covered = all(site_is_covered(p, n, test_texts) for p, n in sites)
    return dict(
        row,
        matches=kept,
        sites=len(sites),
        test_covered=covered,
        # Both copies still present at the end of the session: not a move.
        likely_move=False,
        verdict=verdict(sites=len(sites), n_tokens=len(tokens), covered=covered),
        ts=time.time(),
    )


def _format(item: dict[str, Any]) -> str:
    cover = "test-covered" if item["test_covered"] else "not test-covered"
    lines = [
        f"  [{item['id']}] `{item['added_name']}` ({item['added_path']}) -- "
        f"{item['sites']} sites, {cover}:"
    ]
    lines += [format_match(m) for m in item["matches"]]
    return "\n".join(lines)


def _message(extract: list[dict[str, Any]], notes: list[dict[str, Any]]) -> str:
    parts = [
        "[reasoning-core] DUPLICATE CHECK -- the session cannot end while these are open.",
        "RULE: >= 3 sites + non-trivial + test-covered -> EXTRACT a shared function.",
        "",
        "EXTRACT:",
        *(_format(f) for f in extract),
        "",
        "For each one, either extract it, or record why not:",
        '  rc dup-defer <id> --reason "..."',
    ]
    if notes:
        parts += ["", "Also seen this session (notes, no action required):", *(_format(f) for f in notes)]
    return "\n".join(parts)


def stop_decision(
    repo_root: str, ledger_file: Path, *, max_blocks: int = MAX_BLOCKS, count_blocks: bool = True
) -> StopDecision:
    """Re-check the ledger and decide whether to block the stop.

    Resolved findings are marked in the ledger. Each block of an EXTRACT
    finding is counted on its row; past ``max_blocks`` it is escalated (the
    stop is let through) instead of blocking forever. ``count_blocks=False``
    (advise mode: nothing is actually blocked) leaves the counts untouched.
    """
    # Deferred and escalated findings are re-checked too, but only to resolve
    # them once the duplicate is gone; otherwise their status would outlive the
    # code, and the same copies reintroduced later would stay silent for good.
    rows = [r for r in ledger.load(ledger_file).values() if r["status"] != "resolved"]
    decision = StopDecision(block=False)
    if not rows:
        return decision
    test_texts = load_test_texts(repo_root)
    for row in rows:
        try:
            now = revalidate(row, repo_root, test_texts)
        except (_Unverifiable, UnsupportedLanguageError) as exc:
            # Cannot re-check: keep the finding as recorded, and say so.
            print(f"dup-oracle: cannot re-check finding {row['id']}: {exc}", file=sys.stderr)
            now = row
        if now is None:
            ledger.set_status(ledger_file, row["id"], "resolved", reason=row.get("reason"))
            decision.resolved.append(row["id"])
            continue
        if row["status"] != "open":
            continue
        if now["verdict"] == EXTRACT:
            if now.get("blocks", 0) >= max_blocks:
                # Reported once: the status change takes it out of later reviews.
                decision.escalated.append(now["id"])
                ledger.set_status(ledger_file, now["id"], "escalated")
                continue
            if count_blocks:
                now = dict(now, blocks=now.get("blocks", 0) + 1)
            decision.extract.append(now)
        else:
            decision.notes.append(now)
        ledger.record(ledger_file, now)  # a no-op when nothing changed
    if decision.extract:
        decision.block = True
        decision.message = _message(decision.extract, decision.notes)
    return decision
