"""Unit tests for the repo index builder (src/dup_repo_index.py).

Offline: a deterministic stub embedder (bag of logic tokens) stands in for the
model, so the walk / extract / token-df / cap logic and an end-to-end query over
a built index are exercised with no torch and no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import logic_tokens  # noqa: E402
from src.dup_oracle import find_near_duplicates  # noqa: E402
from src.dup_repo_index import build_dup_index  # noqa: E402

_VOCAB = 64


def _stub_embed(source: str) -> np.ndarray:
    """Deterministic bag-of-logic-tokens vector: identical-logic functions get
    identical vectors (cosine 1.0), different logic diverges. Stable hash."""
    vec = np.zeros(_VOCAB, dtype=np.float32)
    for tok in logic_tokens("x.ts", source):
        vec[sum(ord(c) for c in tok) % _VOCAB] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _make_repo(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (tmp_path / "b.ts").write_text(  # slugify duplicates toSlug (renamed)
        'export function slugify(value) {\n  return value.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (tmp_path / "c.ts").write_text(
        'export function fib(n) {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n}\n'
    )


def test_build_indexes_every_named_function(tmp_path):
    _make_repo(tmp_path)
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    assert len(index) == 3
    assert {r.name for r in index.records} == {"toSlug", "slugify", "fib"}
    assert index.embeddings.shape == (3, _VOCAB)
    assert index.token_df  # populated


def test_query_over_built_index_finds_the_planted_duplicate(tmp_path):
    _make_repo(tmp_path)
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    i = next(k for k, r in enumerate(index.records) if r.name == "toSlug")
    hits = find_near_duplicates(
        index.embeddings[i], index.records[i].logic_tokens, index.records,
        index.embeddings, index.token_df, skip=lambda idx, rec: idx == i,
    )
    assert [h.name for h in hits] == ["slugify"]  # dup found, fib ignored


def test_max_funcs_caps_the_index(tmp_path):
    _make_repo(tmp_path)
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed, max_funcs=2)
    assert len(index) == 2


def test_empty_repo_builds_empty_index(tmp_path):
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    assert len(index) == 0
    assert find_near_duplicates(
        np.zeros(_VOCAB, dtype=np.float32), [], index.records, index.embeddings, index.token_df
    ) == []
