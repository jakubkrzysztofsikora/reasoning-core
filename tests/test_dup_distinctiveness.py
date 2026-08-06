"""Unit tests for the distinctiveness ranking (src/dup_index.py).

Ranks confirmed near-duplicates by how RARE their shared tokens are, so
boilerplate that matches only on ubiquitous tokens sinks below genuine
duplication. Pure / fast: operates on logic-token lists, no parsing, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import (  # noqa: E402
    build_token_df,
    distinctive_shared_tokens,
    distinctiveness,
    rank_by_distinctiveness,
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


def test_boilerplate_pair_scores_zero_distinctiveness():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    assert distinctiveness(corpus[0], corpus[1], df, cutoff) == 0


def test_real_duplicate_shares_distinctive_tokens():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    shared = distinctive_shared_tokens(corpus[3], corpus[4], df, cutoff)
    assert "N:computeTax" in shared and "N:Rate" in shared
    assert "N:super" not in shared  # ubiquitous -> excluded
    assert distinctiveness(corpus[3], corpus[4], df, cutoff) >= 2


def test_ranking_puts_genuine_duplicate_above_boilerplate():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    query = corpus[3]                       # the real-duplicate shape
    candidates = [corpus[0], corpus[4]]     # boilerplate first, genuine second
    ranked = rank_by_distinctiveness(query, candidates, df, cutoff)
    assert ranked[0] is corpus[4]           # genuine duplicate ranks first
    assert ranked[-1] is corpus[0]


def test_rank_supports_a_key_extractor():
    corpus = _corpus()
    df, n = build_token_df(corpus)
    cutoff = rare_cutoff(n)
    hits = [{"name": "boiler", "toks": corpus[0]}, {"name": "real", "toks": corpus[4]}]
    ranked = rank_by_distinctiveness(corpus[3], hits, df, cutoff, key=lambda h: h["toks"])
    assert ranked[0]["name"] == "real"


def test_cutoff_scales_with_repo_size_over_a_floor():
    assert rare_cutoff(10) == 3          # floor wins on a small repo
    assert rare_cutoff(1000) == 30       # 3% on a large one
