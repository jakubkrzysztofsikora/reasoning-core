"""Two-stage near-duplicate query.

Given a query function's embedding + logic tokens and an index of existing repo
functions (embeddings + logic tokens), return the confirmed near-duplicates,
ranked so genuine duplication outranks boilerplate:

    Stage 1 (recall)    cosine shortlist  -- embeddings @ query >= RECALL
    Stage 2 (precision) logic-token diff  -- logic_ratio >= CONFIRM
    Stage 3 (ranking)   distinctiveness   -- most rare-shared-tokens first

Embedding the query function (via ``ssm_backbone``) is the caller's job; given
vectors this module is pure and fully unit-testable without a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from .dup_index import (
    distinctive_shared_tokens,
    logic_ratio_tokens,
    rare_cutoff,
)

RECALL_DEFAULT = 0.80
CONFIRM_DEFAULT = 0.97


@dataclass(frozen=True)
class FunctionRecord:
    """An existing repo function the query is checked against."""

    path: str
    name: str
    lineno: int
    logic_tokens: list[str]


@dataclass(frozen=True)
class DupHit:
    """A confirmed near-duplicate, with the evidence the advisory shows."""

    path: str
    name: str
    lineno: int
    cosine: float
    logic_ratio: float
    shared: list[str]  # distinctive shared tokens -- the "why we think so"


def find_near_duplicates(
    query_vec: "np.ndarray",
    query_tokens: list[str],
    records: list[FunctionRecord],
    embeddings: "np.ndarray",
    token_df: Any,
    *,
    recall: float = RECALL_DEFAULT,
    confirm: float = CONFIRM_DEFAULT,
    cutoff: Optional[int] = None,
    skip: Optional[Callable[[int, FunctionRecord], bool]] = None,
) -> list[DupHit]:
    """Confirmed near-duplicates of the query among ``records``, most-distinctive
    first.

    ``query_vec`` and every row of ``embeddings`` must be L2-normalised (so
    cosine == dot product); ``embeddings`` rows align to ``records``. ``skip``
    lets the caller drop a record (e.g. the query's own entry on a re-edit).
    """
    n = len(records)
    if n == 0:
        return []
    if cutoff is None:
        cutoff = rare_cutoff(n)

    cosines = np.asarray(embeddings) @ np.asarray(query_vec)
    hits: list[DupHit] = []
    for i in range(n):
        cos = float(cosines[i])
        if cos < recall:  # Stage 1: recall shortlist
            continue
        if skip is not None and skip(i, records[i]):
            continue
        ratio = logic_ratio_tokens(query_tokens, records[i].logic_tokens)
        if ratio < confirm:  # Stage 2: precision confirm
            continue
        shared = distinctive_shared_tokens(
            query_tokens, records[i].logic_tokens, token_df, cutoff
        )
        rec = records[i]
        hits.append(DupHit(rec.path, rec.name, rec.lineno, cos, ratio, shared))

    # Stage 3: rank -- most distinctive shared tokens first, then logic, then cosine.
    hits.sort(key=lambda h: (len(h.shared), h.logic_ratio, h.cosine), reverse=True)
    return hits
