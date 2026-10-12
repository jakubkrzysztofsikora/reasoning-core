"""Decide what to do with confirmed duplicate hits.

Pure over ``DupHit`` lists + the set of files in the working diff, so the
per-edit and Stop hooks share one rule set and it is testable without a model.
"""
from __future__ import annotations

from .oracle import DupHit


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
