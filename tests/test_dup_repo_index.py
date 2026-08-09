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

import pytest  # noqa: E402

from src.dup_index import extract_functions, logic_tokens  # noqa: E402
from src.dup_oracle import find_near_duplicates  # noqa: E402
from src.dup_repo_index import build_dup_index  # noqa: E402

_VOCAB = 64


def _extraction_works(rel: str, sample: str) -> bool:
    """True only if the extractor actually returns a function for ``sample`` --
    a loadable grammar alone is not enough (the node types must be in
    dup_index._FUNC_TYPES), so this guards against a false-positive skip."""
    try:
        return bool(extract_functions(rel, sample))
    except Exception:
        return False


# A substantial (>=40 char body) C# method so the size filter doesn't drop it.
HAS_CSHARP = _extraction_works(
    "x.cs",
    "class C {\n  public int Add(int a, int b) { return a + b + a - b; }\n}\n",
)


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


def test_max_funcs_caps_within_a_single_file(tmp_path):
    # Many functions in ONE file, cap below that -> the inner (per-function) cap
    # must bound the index, not just the per-file check.
    fns = "\n".join(
        f"export function fn{i}(x) {{\n  return x.step{i}().done{i}();\n}}" for i in range(6)
    )
    (tmp_path / "many.ts").write_text(fns + "\n")
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed, max_funcs=3)
    assert len(index) == 3


def test_index_covers_mjs_and_cjs(tmp_path):
    # The grammars support .mjs/.cjs (both -> javascript), so duplicates in those
    # sources must be indexed. The old discovery only walked .py/.js/.ts/.tsx and
    # silently missed them.
    (tmp_path / "a.mjs").write_text("export function slugM(s) {\n  return s.toLowerCase().trim();\n}\n")
    (tmp_path / "b.cjs").write_text(
        "function slugC(s) {\n  return s.toLowerCase().trim();\n}\nmodule.exports = { slugC };\n"
    )
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    assert {"slugM", "slugC"} <= {r.name for r in index.records}


def test_index_build_survives_a_file_whose_grammar_is_missing(tmp_path):
    # A supported-but-uninstalled grammar (e.g. C# where the wheel is absent)
    # raises at extract time. That must degrade to "skip this file", never crash
    # the whole build -- the JS functions must still be indexed.
    (tmp_path / "a.mjs").write_text(
        "export function keep(s) {\n  return s.toLowerCase().replace(RE, '-').trim();\n}\n"
    )
    (tmp_path / "weird.cs").write_text("class C {\n  int Add(int a, int b) { return a + b; }\n}\n")
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)  # must not raise
    assert "keep" in {r.name for r in index.records}


def test_index_skips_a_file_whose_grammar_is_not_installed(tmp_path, monkeypatch):
    # Deterministic (grammar-agnostic) version of the degradation guard: force
    # ONE file's extraction to raise the grammar-not-installed RuntimeError and
    # assert the build survives and still indexes the other file.
    import src.dup_repo_index as dri

    (tmp_path / "a.mjs").write_text(
        "export function keep(s) {\n  return s.toLowerCase().replace(RE, '-').trim();\n}\n"
    )
    (tmp_path / "b.mjs").write_text(
        "export function other(s) {\n  return s.toUpperCase().split(',').join('-');\n}\n"
    )
    real = dri.extract_functions

    def flaky(rel, src):
        if rel.endswith("b.mjs"):
            raise RuntimeError("Could not load tree-sitter grammar for 'javascript'. Install ...")
        return real(rel, src)

    monkeypatch.setattr(dri, "extract_functions", flaky)
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    names = {r.name for r in index.records}
    assert "keep" in names and "other" not in names


def test_index_build_does_not_swallow_a_systemic_grammar_error(tmp_path, monkeypatch):
    # A systemic failure (tree-sitter ABI mismatch) affects every file. It must
    # NOT be silently turned into an empty index -- the oracle would then report
    # "no duplicates" forever with zero signal. It must surface.
    import src.dup_repo_index as dri

    (tmp_path / "a.mjs").write_text(
        "export function keep(s) {\n  return s.toLowerCase().replace(RE, '-').trim();\n}\n"
    )

    def boom(rel, src):
        raise RuntimeError("tree-sitter ABI mismatch for 'javascript': ... upgrade tree-sitter")

    monkeypatch.setattr(dri, "extract_functions", boom)
    with pytest.raises(RuntimeError):
        build_dup_index(str(tmp_path), embed_fn=_stub_embed)


@pytest.mark.skipif(not HAS_CSHARP, reason="tree-sitter C# grammar not installed")
def test_index_covers_csharp_when_grammar_available(tmp_path):
    (tmp_path / "a.cs").write_text(
        "class Calc {\n  public int Add(int a, int b) { return a + b; }\n}\n"
    )
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    assert any(r.name == "Add" for r in index.records)


def test_build_indexes_across_languages(tmp_path):
    # The builder is language-agnostic (tree-sitter via select_grammar): a Python
    # AND a TypeScript function in the same repo should both be indexed. Nothing
    # else exercises the non-TS path.
    (tmp_path / "a.ts").write_text('export function tsOne(x) {\n  return x.toLowerCase().trim();\n}\n')
    (tmp_path / "b.py").write_text('def py_one(x):\n    return x.lower().strip()\n')
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    assert {r.name for r in index.records} == {"tsOne", "py_one"}
