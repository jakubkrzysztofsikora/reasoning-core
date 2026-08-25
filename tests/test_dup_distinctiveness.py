"""Unit tests for the distinctiveness primitives (src/dup_index.py).

These rank confirmed near-duplicates by how RARE their shared tokens are, so
boilerplate that matches only on ubiquitous tokens sinks below genuine
duplication. The ranking is applied on the production path by
``dup_oracle.find_near_duplicates`` (see test_dup_oracle_query.py); here we pin
the primitives it uses. Pure / fast: logic-token lists, no parsing, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collections import Counter  # noqa: E402

from src.dup_index import (  # noqa: E402
    build_token_df,
    distinctive_shared_tokens,
    rare_cutoff,
)


def _corpus() -> list[list[str]]:
    # A repo where `super`/`this` are ubiquitous (boilerplate) while a couple of
    # callees are rare (genuine signal).
    return [
        ["N:super", "N:this"],                              # boilerplate 1
        ["N:super", "N:this"],                              # boilerplate 2
        ["N:super", "N:this"],                              # boilerplate 3
        ["N:super", "N:this", "N:computeTax", "N:Rate"],    # real dup A
        ["N:super", "N:this", "N:computeTax", "N:Rate"],    # real dup B
        ["N:foo"], ["N:bar"], ["N:baz"], ["N:qux"], ["N:quux"],  # filler
    ]


def test_common_tokens_are_not_distinctive():
    df, n = build_token_df(_corpus())
    cutoff = rare_cutoff(n)
    # super/this appear in 5 of 10 functions -> above cutoff -> not distinctive.
    assert df["N:super"] > cutoff
    assert df["N:this"] > cutoff
    # a callee in only 2 functions -> distinctive.
    assert df["N:computeTax"] <= cutoff


def test_boilerplate_pair_shares_no_distinctive_tokens():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    # Boilerplate matches only on ubiquitous tokens -> nothing distinctive shared.
    assert distinctive_shared_tokens(corpus[0], corpus[1], df, cutoff) == []


def test_real_duplicate_shares_distinctive_tokens():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    shared = distinctive_shared_tokens(corpus[3], corpus[4], df, cutoff)
    assert "N:computeTax" in shared and "N:Rate" in shared
    assert "N:super" not in shared  # ubiquitous -> excluded
    assert len(shared) >= 2


def test_cutoff_makes_a_token_distinctive_only_at_repo_scale():
    # A token seen in 5 functions is "common" in a tiny repo but "rare" in a big
    # one -- the cutoff must flip the distinctiveness verdict as the repo grows.
    shared, df = ["N:sharedTok"], Counter({"N:sharedTok": 5})
    assert distinctive_shared_tokens(shared, shared, df, rare_cutoff(10)) == []  # df 5 > cutoff 3
    assert distinctive_shared_tokens(shared, shared, df, rare_cutoff(1000)) == ["N:sharedTok"]  # df 5 <= cutoff 30
