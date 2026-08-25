"""Headline integration test: the date-fns reproduction, run OFFLINE.

Over a frozen, pinned snapshot of date-fns core (~265 functions + their
embeddings -- see tests/fixtures/dup_oracle/PROVENANCE.md), the two-stage oracle
must spotlight exactly the one real duplicate: `cleanEscapedString`, copy-pasted
across format / lightFormat / parse -- and confirm nothing else.

Uses the frozen `.npy` vectors, so no model and no network: runs in the CI
`-m "not live"` offline gate. Regenerate the fixture with the generator noted in
PROVENANCE.md (the only place the model runs).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import build_token_df, logic_tokens  # noqa: E402
from src.dup_oracle import FunctionRecord, find_near_duplicates  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dup_oracle"


def _load():
    data = json.loads((FIXTURE / "date_fns_functions.json").read_text())
    vectors = np.load(FIXTURE / "date_fns_vectors.npy")
    records = [
        FunctionRecord(r["path"], r["name"], r["line"], logic_tokens(r["path"], r["source"]))
        for r in data
    ]
    token_df, _ = build_token_df([r.logic_tokens for r in records])
    return records, vectors, token_df


def _confirmed_pairs(records, vectors, token_df) -> set:
    """All confirmed cross-file duplicate pairs, as {frozenset{(name, path)}}."""
    pairs: set = set()
    for i in range(len(records)):
        hits = find_near_duplicates(
            vectors[i], records[i].logic_tokens, records, vectors, token_df,
            skip=lambda idx, rec, i=i: idx == i,
        )
        for h in hits:
            pairs.add(frozenset([(records[i].name, records[i].path), (h.name, h.path)]))
    return pairs


def test_fixture_contains_the_hard_negatives():
    # `test_sibling_families_are_never_confirmed` below is only meaningful if the
    # corpus actually CONTAINS those siblings -- otherwise it passes vacuously.
    # Guard their presence (and that vectors stay row-aligned to records).
    records, vectors, _ = _load()
    assert len(records) == len(vectors)  # row-aligned
    names = {r.name for r in records}
    missing = {"min", "max", "addDays", "addHours", "addWeeks", "subWeeks"} - names
    assert not missing, f"hard-negative siblings missing from fixture: {missing}"


def test_spotlights_only_the_cleanEscapedString_duplicate():
    records, vectors, token_df = _load()
    names = {name for pair in _confirmed_pairs(records, vectors, token_df) for name, _ in pair}
    assert names == {"cleanEscapedString"}


def test_finds_the_full_three_file_cluster():
    records, vectors, token_df = _load()
    files = {path for pair in _confirmed_pairs(records, vectors, token_df) for _, path in pair}
    assert files == {"format/index.ts", "lightFormat/index.ts", "parse/index.ts"}


def test_sibling_families_are_never_confirmed():
    records, vectors, token_df = _load()
    involved = {name for pair in _confirmed_pairs(records, vectors, token_df) for name, _ in pair}
    for sibling in (
        "min", "max", "addDays", "addHours", "addWeeks", "subWeeks",
        "startOfWeek", "endOfWeek", "startOfQuarter", "endOfQuarter",
    ):
        assert sibling not in involved
