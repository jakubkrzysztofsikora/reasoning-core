"""Tests for the persisted dup-oracle index (TB-94).

Offline: a deterministic, call-counting stub embedder stands in for the model,
so the disk-cache reuse/invalidation/fallback logic is exercised with no torch
and no network. Every test passes its own tmp ``cache_dir`` so nothing is written
to the real ``~/.cache`` during the suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from src.dup_index import logic_tokens  # noqa: E402
from src.dup_oracle import FunctionRecord  # noqa: E402

_VOCAB = 64


class CountingEmbed:
    """Deterministic bag-of-logic-tokens embedder that counts how many function
    sources it embeds -- the probe for "did we re-embed or reuse?"."""

    def __init__(self, vocab: int = _VOCAB) -> None:
        self.vocab = vocab
        self.calls = 0

    def __call__(self, source: str) -> np.ndarray:
        self.calls += 1
        vec = np.zeros(self.vocab, dtype=np.float32)
        for tok in logic_tokens("x.ts", source):
            vec[sum(ord(c) for c in tok) % self.vocab] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec


def _make_repo(root: Path) -> None:
    (root / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (root / "b.ts").write_text(
        'export function slugify(value) {\n  return value.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (root / "c.ts").write_text(
        'export function fib(n) {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n}\n'
    )


# --------------------------------------------------------------------------
# Increment 1 -- the _index_file seam
# --------------------------------------------------------------------------

def test_index_file_returns_records_and_row_aligned_vectors(tmp_path):
    from src.dup_repo_index import _index_file

    _make_repo(tmp_path)
    embed = CountingEmbed()
    records, vecs = _index_file(str(tmp_path), "a.ts", embed)

    assert [r.name for r in records] == ["toSlug"]
    assert all(isinstance(r, FunctionRecord) for r in records)
    assert len(vecs) == len(records)  # row-aligned
    assert vecs[0].shape == (_VOCAB,)
    assert embed.calls == 1  # one function embedded


def test_index_file_skips_unsupported_language(tmp_path):
    from src.dup_repo_index import _index_file

    # An extension the grammar layer doesn't map at all raises
    # UnsupportedLanguageError -> skipped. (The real walk only feeds in-scope
    # code files; this is the defensive branch.)
    (tmp_path / "notes.txt").write_text("just some prose, not code\n")
    records, vecs = _index_file(str(tmp_path), "notes.txt", CountingEmbed())
    assert records == []
    assert vecs == []


def test_index_file_propagates_systemic_grammar_error(tmp_path, monkeypatch):
    import src.dup_repo_index as dri

    (tmp_path / "a.ts").write_text(
        'export function keep(s) {\n  return s.toLowerCase().trim();\n}\n'
    )

    def boom(rel, src):
        raise RuntimeError("tree-sitter ABI mismatch for 'typescript'")

    monkeypatch.setattr(dri, "extract_functions", boom)
    with pytest.raises(RuntimeError):
        dri._index_file(str(tmp_path), "a.ts", CountingEmbed())
