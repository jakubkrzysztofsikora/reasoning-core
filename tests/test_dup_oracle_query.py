"""Unit tests for the two-stage near-duplicate query (src/dup_oracle.py).

Pure / fast: hand-made L2-normalised vectors + logic-token lists, no model.
Exercises Stage 1 (cosine recall), Stage 2 (logic-diff precision) and Stage 3
(distinctiveness ranking) together.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import build_token_df  # noqa: E402
from src.dup_oracle import FunctionRecord, find_near_duplicates  # noqa: E402


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


# Query: a function whose logic is [computeTax, Rate, *].
QUERY_VEC = _unit([1.0, 0.0, 0.0])
QUERY_TOKENS = ["N:computeTax", "N:Rate", "K:*"]

# Records: a true duplicate (same logic, near vector), a sibling (near vector,
# different logic), and an unrelated function (far vector).
RECORDS = [
    FunctionRecord("dup.ts", "taxOf", 10, ["N:computeTax", "N:Rate", "K:*"]),
    FunctionRecord("sibling.ts", "feeOf", 20, ["N:computeFee", "K:+"]),
    FunctionRecord("far.ts", "unrelated", 30, ["N:foo"]),
]
EMBEDDINGS = np.stack([
    _unit([0.99, 0.01, 0.0]),   # dup     -> cosine ~1.0  (shortlisted, confirmed)
    _unit([0.90, 0.10, 0.0]),   # sibling -> cosine ~0.99 (shortlisted, rejected)
    _unit([0.0, 1.0, 0.0]),     # far     -> cosine ~0.0  (not shortlisted)
])
DF, _N = build_token_df([r.logic_tokens for r in RECORDS])


def test_confirms_dup_rejects_sibling_ignores_unrelated():
    hits = find_near_duplicates(QUERY_VEC, QUERY_TOKENS, RECORDS, EMBEDDINGS, DF)
    assert [h.name for h in hits] == ["taxOf"]
    assert hits[0].logic_ratio == 1.0
    assert hits[0].cosine >= 0.80


def test_hit_carries_shared_distinctive_tokens():
    hits = find_near_duplicates(QUERY_VEC, QUERY_TOKENS, RECORDS, EMBEDDINGS, DF)
    assert "N:computeTax" in hits[0].shared and "N:Rate" in hits[0].shared


def test_ranks_by_cosine_when_logic_and_distinctiveness_tie():
    # Two true duplicates, identical logic -> tie on logic + shared tokens, so
    # cosine breaks the tie (higher first).
    records = [
        FunctionRecord("hi.ts", "hi", 1, QUERY_TOKENS),
        FunctionRecord("lo.ts", "lo", 1, QUERY_TOKENS),
    ]
    embeddings = np.stack([_unit([0.95, 0.31, 0.0]), _unit([0.999, 0.03, 0.0])])
    df, _ = build_token_df([r.logic_tokens for r in records])
    hits = find_near_duplicates(QUERY_VEC, QUERY_TOKENS, records, embeddings, df)
    assert [h.name for h in hits] == ["lo", "hi"]  # lo has the higher cosine


def test_skip_excludes_a_record():
    # Skip the dup by index -> nothing confirmed remains.
    hits = find_near_duplicates(
        QUERY_VEC, QUERY_TOKENS, RECORDS, EMBEDDINGS, DF,
        skip=lambda i, rec: rec.path == "dup.ts",
    )
    assert hits == []


def test_empty_index_returns_empty():
    hits = find_near_duplicates(QUERY_VEC, QUERY_TOKENS, [], np.empty((0, 3)), DF)
    assert hits == []


def test_below_recall_is_never_confirmed_even_if_logic_matches():
    # Far vector but IDENTICAL logic -> Stage 1 must gate it out (no false hit).
    records = [FunctionRecord("far.ts", "same", 1, QUERY_TOKENS)]
    embeddings = np.stack([_unit([0.0, 1.0, 0.0])])  # cosine ~0 with query
    df, _ = build_token_df([r.logic_tokens for r in records])
    hits = find_near_duplicates(QUERY_VEC, QUERY_TOKENS, records, embeddings, df)
    assert hits == []
