"""Unit tests for the advisory hook's pure pieces (src/hooks/pre_edit_dup_advisory.py).

Offline: a stub embedder + a stub-built index, no torch, no stdin. Covers the
added-source extraction and the advise() decision (flags a duplicate, silent on
novel code).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dup_index import logic_tokens  # noqa: E402
from src.dup_repo_index import build_dup_index  # noqa: E402
from src.hooks.pre_edit_dup_advisory import _added_source, advise  # noqa: E402

_VOCAB = 64


def _stub_embed(source: str) -> np.ndarray:
    vec = np.zeros(_VOCAB, dtype=np.float32)
    for tok in logic_tokens("x.ts", source):
        vec[sum(ord(c) for c in tok) % _VOCAB] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _index(tmp_path: Path):
    (tmp_path / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (tmp_path / "c.ts").write_text(
        'export function fib(n) {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n}\n'
    )
    return build_dup_index(str(tmp_path), embed_fn=_stub_embed)


def test_added_source_handles_write_edit_and_multiedit():
    assert _added_source({"content": "WRITE"}) == "WRITE"
    assert _added_source({"new_string": "EDIT"}) == "EDIT"
    both = _added_source({"edits": [{"new_string": "A"}, {"new_string": "B"}]})
    assert "A" in both and "B" in both
    assert _added_source({}) == ""


def test_advise_flags_a_function_that_duplicates_an_indexed_one(tmp_path):
    index = _index(tmp_path)
    # Agent writes a new file whose function duplicates toSlug's logic.
    added = 'export function makeSlug(t) {\n  return t.toLowerCase().replace(RE, "-").trim();\n}\n'
    text = advise(added, "d.ts", index, embed_fn=_stub_embed)
    assert text is not None
    assert "makeSlug" in text and "toSlug" in text
    assert "advisory only" in text


def test_advise_is_silent_on_a_novel_function(tmp_path):
    index = _index(tmp_path)
    added = (
        "export function quicksort(arr) {\n"
        "  if (arr.length < 2) return arr;\n"
        "  const p = arr[0];\n"
        "  return quicksort(arr.filter((x) => x < p));\n"
        "}\n"
    )
    assert advise(added, "d.ts", index, embed_fn=_stub_embed) is None


def test_advise_empty_index_or_source_is_silent(tmp_path):
    index = _index(tmp_path)
    assert advise("", "d.ts", index, embed_fn=_stub_embed) is None  # no source
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty_index = build_dup_index(str(empty_dir), embed_fn=_stub_embed)
    assert advise("export function f(){ return 1 + 1 + 1; }", "d.ts", empty_index, embed_fn=_stub_embed) is None
