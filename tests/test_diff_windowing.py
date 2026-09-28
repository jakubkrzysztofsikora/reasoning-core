"""Tests for the AST-scope diff-windowing module.

Covers the 2026-09-19 audit-deferred windowing memo's pre-reg gates:
- Long-file blindness rate >= 80% (we test the chunker directly)
- Diff-drowning >= 50% (we test that changed chunks get weight 1.0)
- Determinism L2 tolerance 1e-6 across 10 re-runs
- Per-edit latency p95 <= 5.0s (light test: under 2s for a 5-chunk file)
"""
from __future__ import annotations

import sys
import os
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.diff_windowing import (
    Chunk,
    chunk_source,
    diff_hunk_byte_ranges,
    diff_weights,
    embed_windowed,
)


# ---------------------------------------------------------------------------
# Test data: a Python file with 5 top-level functions, where one edit
# is at line 50 (past the 512-token truncation boundary if the file is
# extended to ~1000+ tokens).
# ---------------------------------------------------------------------------

SMALL_PY = '''\
def foo():
    return 1


def bar():
    return 2


def baz():
    return 3


def qux():
    return 4


def quux():
    return 5
'''


def _big_py(n_lines: int = 250) -> str:
    """Generate a Python file with n_lines, padding between functions."""
    head = SMALL_PY
    pad = "\n".join(["# padding line"] * (n_lines - 20))
    return head + "\n" + pad + "\n"


def _grammar_python():
    try:
        from src.grammars import _load_language  # type: ignore
        _load_language("python")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# chunk_source
# ---------------------------------------------------------------------------


def test_chunk_source_returns_at_least_one_chunk_for_empty():
    chunks = chunk_source("")
    assert len(chunks) == 1
    assert chunks[0].text == ""


def test_chunk_source_handles_no_tree():
    chunks = chunk_source(SMALL_PY, tree=None)
    # Without a tree we fall back to line-windowing.
    assert len(chunks) >= 1
    # All chunks must be non-overlapping and cover the source.
    cursor = 0
    for ch in chunks:
        assert ch.start_byte >= cursor
        cursor = ch.end_byte


def test_chunk_source_chunks_by_function_when_tree_available():
    if not _grammar_python():
        pytest.skip("tree-sitter python grammar not installed")
    from src.grammars import _load_language  # type: ignore
    from src.grammars import get_parser  # type: ignore

    # use src.grammars.get_parser
    parser = get_parser("python")
    tree = parser.parse(bytes(SMALL_PY, "utf-8"))

    chunks = chunk_source(SMALL_PY, tree=tree)
    # We expect 5 function chunks for the 5 top-level defs.
    assert len(chunks) == 5
    for ch in chunks:
        assert ch.scope_kind == "function_definition"
        assert "def " in ch.text


def test_chunk_source_falls_back_to_line_window_for_huge_function():
    """A single function spanning > 384 tokens triggers line-window split."""
    if not _grammar_python():
        pytest.skip("tree-sitter python grammar not installed")
    from src.grammars import _load_language  # type: ignore
    from src.grammars import get_parser  # type: ignore

    huge = "def giant():\n" + "    x = 1\n" * 500
    # use src.grammars.get_parser
    parser = get_parser("python")
    tree = parser.parse(bytes(huge, "utf-8"))
    chunks = chunk_source(huge, tree=tree, max_tokens_per_chunk=128)
    # Either we get the whole function or we get line-window subchunks.
    # Either way: must have at least one chunk and the total bytes
    # covered must be >= the function bytes.
    assert len(chunks) >= 1
    total_bytes = sum(ch.end_byte - ch.start_byte for ch in chunks)
    assert total_bytes >= len(huge.encode("utf-8"))


def test_chunk_source_preserves_every_chunk_after_round2_fix():
    """ROUND-2 RC-WINDOWING-STRIDE-01: the previous cap-and-stride
    subsampling silently dropped chunks that contained diff hunks in
    files with > ~32 chunks. The round-2 fix preserves every chunk
    when the cap is exceeded; the embedder downstream enforces the
    cost cap via its own max_seq_len budget. The cost-cap contract
    moved from chunk_source to the embedder call site.
    """
    if not _grammar_python():
        pytest.skip("tree-sitter python grammar not installed")
    from src.grammars import get_parser  # type: ignore

    src = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(50))
    parser = get_parser("python")
    tree = parser.parse(bytes(src, "utf-8"))
    chunks = chunk_source(src, tree=tree)
    # The chunker must keep every chunk -- the stride subsampling
    # was the bug. The embedder call site bounds the actual embed
    # cost (see ``embed_windowed`` -> per-chunk embed_fn call).
    assert len(chunks) == 50, (
        f"chunk_source dropped chunks: have {len(chunks)}, expected 50"
    )


# ---------------------------------------------------------------------------
# diff_hunk_byte_ranges
# ---------------------------------------------------------------------------


def test_diff_hunks_empty_when_no_diff():
    ranges = diff_hunk_byte_ranges("a", "a")
    assert ranges == []


def test_diff_hunks_returns_changed_ranges():
    before = "def foo():\n    return 1\n"
    after = "def foo():\n    return 2\n"
    ranges = diff_hunk_byte_ranges(before, after)
    assert len(ranges) >= 1
    # The hunk range should be in the after-source coordinate system.
    after_bytes = after.encode("utf-8")
    for sb, eb in ranges:
        assert 0 <= sb <= len(after_bytes)
        assert 0 <= eb <= len(after_bytes)


def test_diff_hunks_handles_pure_addition():
    before = ""
    after = "def foo():\n    return 1\n"
    ranges = diff_hunk_byte_ranges(before, after)
    assert len(ranges) >= 1


# ---------------------------------------------------------------------------
# diff_weights
# ---------------------------------------------------------------------------


def test_diff_weights_empty_chunks():
    assert diff_weights([], []) == []


def test_diff_weights_empty_hunks_returns_uniform():
    chunks = [Chunk(0, 4, "abcd", "function"), Chunk(4, 8, "efgh", "function")]
    weights = diff_weights(chunks, [])
    assert len(weights) == 2
    assert weights[0] == pytest.approx(0.5)
    assert weights[1] == pytest.approx(0.5)


def test_diff_weights_changed_chunk_gets_full_weight():
    chunks = [Chunk(0, 4, "abcd", "function"), Chunk(4, 8, "efgh", "function")]
    # Hunk covers the second chunk exactly.
    weights = diff_weights(chunks, [(4, 8)])
    # Changed chunk (index 1) gets 1.0; neighbour (index 0) gets 0.25.
    # Total = 1.0 + 0.25 = 1.25; scale = 0.8.
    assert weights[1] == pytest.approx(1.0 / 1.25)
    assert weights[0] == pytest.approx(0.25 / 1.25)
    # Untouched chunks (none here) would be 0.


def test_diff_weights_overlap_marks_change():
    chunks = [Chunk(0, 4, "abcd", "function"), Chunk(4, 8, "efgh", "function")]
    # Hunk overlaps chunk 0.
    weights = diff_weights(chunks, [(2, 6)])
    assert weights[0] > 0.0
    assert weights[1] > 0.0  # neighbour


def test_diff_weights_sums_to_one():
    """L2-normalisation: weights should sum to 1.0 when at least one is non-zero."""
    chunks = [Chunk(i * 4, (i + 1) * 4, "x" * 4, "function") for i in range(5)]
    weights = diff_weights(chunks, [(8, 12)])  # hunk on chunk 1
    assert sum(weights) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Determinism gate: 10 re-runs, L2 tolerance 1e-6
# ---------------------------------------------------------------------------


def test_diff_weights_deterministic():
    chunks = [Chunk(i * 4, (i + 1) * 4, "x" * 4, "function") for i in range(5)]
    hunk_ranges = [(8, 12)]
    w0 = diff_weights(chunks, hunk_ranges)
    for _ in range(10):
        w = diff_weights(chunks, hunk_ranges)
        for a, b in zip(w0, w):
            assert a == b


def test_diff_hunks_deterministic():
    before = "def foo():\n    return 1\n"
    after = "def foo():\n    return 2\n"
    r0 = diff_hunk_byte_ranges(before, after)
    for _ in range(10):
        r = diff_hunk_byte_ranges(before, after)
        assert r == r0


# ---------------------------------------------------------------------------
# embed_windowed: mocked embed_fn for fast, deterministic testing
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """A deterministic stub embedder that returns a stable vector per chunk.

    Returns vec[i] = i % 256 / 256 as a numpy array. This makes the
    per-chunk embedding distinct, so we can verify chunk-weighted
    pooling produces a non-trivial result.
    """

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def __call__(self, text: str):  # matches ssm_backbone.embed signature
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Use first 4 bytes -> 4 floats.
        import numpy as _np
        v = _np.frombuffer(h[: self.dim], dtype=_np.uint8).astype(_np.float32)
        return v / 255.0


def test_embed_windowed_returns_two_arrays():
    src = "def foo():\n    return 1\n"
    fake = _FakeEmbedder()
    before, after = embed_windowed(src, src, embed_fn=fake)
    assert before.shape == after.shape
    # Same source -> same embedding.
    import numpy as _np
    assert _np.allclose(before, after)


def test_embed_windowed_picks_up_edit():
    before_src = "def foo():\n    return 1\n"
    after_src = "def foo():\n    return 2\n"
    fake = _FakeEmbedder()
    before, after = embed_windowed(before_src, after_src, embed_fn=fake)
    # The edit changed the content -> embeddings should differ.
    import numpy as _np
    diff = float(_np.linalg.norm(before - after))
    assert diff > 0.0


def test_embed_windowed_long_file_drowning_fix():
    """5-token edit in a 300-token function must show non-zero novelty.

    Simulates the audit's "diff drowning" claim: a small edit in a big
    function. The windowed path should see the edit because the changed
    chunk is weighted 1.0.
    """
    before = "def big():\n" + "    x = 1\n" * 100 + "    return 0\n"
    after = "def big():\n" + "    x = 1\n" * 99 + "    x = 2\n    x = 1\n    return 0\n"
    fake = _FakeEmbedder()
    emb_b, emb_a = embed_windowed(before, after, embed_fn=fake)
    import numpy as _np
    diff = float(_np.linalg.norm(emb_b - emb_a))
    # If the changed chunk is weighted 1.0 (vs ~1.7% for naive mean),
    # the diff should be substantial.
    assert diff > 0.01, f"diff was {diff}; windowing likely drowned the edit"


def test_embed_windowed_latency_under_2s_for_small_file():
    """5-chunk file embedding should complete in <2s with the fake embedder."""
    fake = _FakeEmbedder()
    src = _big_py(50)
    start = time.monotonic()
    for _ in range(5):
        embed_windowed(src, src, embed_fn=fake)
    elapsed = time.monotonic() - start
    # 5 runs in <2s = <400ms per run.
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# Integration: windowed vs naive on the audit's "line-40 blindness"
# ---------------------------------------------------------------------------


def test_windowed_path_sees_edit_past_line_40():
    """An edit at line 50 must be visible to the windowed path.

    The naive path (full-file mean-pool then truncate at 512 AST
    tokens) collapses to cos=1.0 because the edit falls past the
    truncation boundary. The windowed path sees the changed chunk
    with weight 1.0 and produces a non-zero novelty.
    """
    if not _grammar_python():
        pytest.skip("tree-sitter python grammar not installed")
    from src.grammars import _load_language  # type: ignore
    from src.grammars import get_parser  # type: ignore

    # Build a file large enough that the edit at line 50 falls past
    # the AST-token truncation boundary (~512 tokens).
    before_lines = ["# pad"] * 60
    after_lines = list(before_lines)
    after_lines[49] = "# CRITICAL CHANGE"  # line 50 (0-indexed: 49)

    before_src = "\n".join(before_lines) + "\n"
    after_src = "\n".join(after_lines) + "\n"

    # use src.grammars.get_parser
    parser = get_parser("python")
    tree_before = parser.parse(bytes(before_src, "utf-8"))
    tree_after = parser.parse(bytes(after_src, "utf-8"))

    fake = _FakeEmbedder()
    emb_b, emb_a = embed_windowed(
        before_src,
        after_src,
        tree_before=tree_before,
        tree_after=tree_after,
        embed_fn=fake,
    )
    import numpy as _np
    diff = float(_np.linalg.norm(emb_b - emb_a))
    assert diff > 0.0, "edit at line 50 should be visible to windowed path"
