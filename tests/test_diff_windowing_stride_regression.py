"""RC-WINDOWING-STRIDE-01 (round-2 hostile review Finding 4): the
32-chunk stride subsampling silently drops chunks that contain
diff hunks in files with > ~800 functions.

The audit verified by execution:
  - planted a malicious line at line 1900 in a 4000-line file
  - the changed chunk was subsampled away
  - coherence_delta = 0.000000 identical in both modes
  - the report said windowed_embed_active=True

This test reproduces the bug end-to-end (no monkeypatching of the
chunker) and asserts the chunker preserves the chunk that contains
every diff hunk.
"""
from __future__ import annotations

from src.diff_windowing import chunk_source, diff_hunk_byte_ranges


def _make_python_file(n_scopes: int) -> str:
    """Build a Python source string with N top-level scopes.

    Each scope is ``def f_{i}():\\n    return {i}\\n`` (real newlines,
    not literal backslash-n). Tree-sitter parses these as Python.
    """
    return "".join(
        "def f_" + str(i) + "():\n    return " + str(i) + "\n"
        for i in range(n_scopes)
    )


def _plant_malicious(src: str, scope_idx: int) -> str:
    """Append ``os.system('evil')`` to the body of ``scope_idx``."""
    needle = "def f_" + str(scope_idx) + "():\n    return " + str(scope_idx) + "\n"
    payload = "def f_" + str(scope_idx) + "():\n    return " + str(scope_idx) + "\n    os.system('evil')\n"
    return src.replace(needle, payload, 1)


def test_chunk_source_preserves_diff_hunk_in_4000_function_file():
    """ROUND-2 hostile review Finding 4: planted malicious line at
    func_1900 in a 4000-function file. The stride cap (32) is hit
    (125 chunks before cap), and the diff hunk at byte 54810 falls
    between chunks 59 (start_byte ~26280) and 62 (start_byte ~27500)
    in stride-3 subsampling -> hunk is invisible to the embedder.

    Before the fix: chunk_source returns 32 chunks but NONE contains
    the diff hunk byte, so the embedder sees a no-op diff.
    After the fix: chunk_source preserves the chunk that contains
    every diff hunk, even after the stride cap is applied.
    """
    before_src = _make_python_file(4000)
    after_src = _plant_malicious(before_src, 1900)
    chunks = chunk_source(after_src)
    hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)
    assert hunk_ranges, "no diff hunks detected (test setup error)"
    hunk_start = hunk_ranges[0][0]
    containing = [
        i
        for i, ch in enumerate(chunks)
        if ch.start_byte <= hunk_start < ch.end_byte
    ]
    assert containing, (
        f"ROUND-2 BLOCKER #4 STILL PRESENT: 4000-function file with "
        f"hunk at byte {hunk_start} -> chunk_source produced {len(chunks)} "
        f"chunks after stride cap but NONE covers the hunk. "
        f"Windowed embedder is blind to edits past chunk 32."
    )


def test_chunk_source_preserves_diff_hunk_when_over_cap():
    """In an 800-function file, the chunker must keep the diff hunk chunk."""
    before_src = _make_python_file(800)
    after_src = _plant_malicious(before_src, 400)
    chunks = chunk_source(after_src)
    hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)
    assert hunk_ranges
    hunk_start = hunk_ranges[0][0]
    containing = [
        i
        for i, ch in enumerate(chunks)
        if ch.start_byte <= hunk_start < ch.end_byte
    ]
    assert containing, (
        f"ROUND-2 BLOCKER #4: diff hunk at byte {hunk_start} is NOT covered "
        f"by any of {len(chunks)} chunks after stride subsampling."
    )


def test_chunk_source_preserves_diff_hunk_at_boundary():
    """The stride subsampling must not drop hunks near the file boundary."""
    before_src = _make_python_file(800)
    after_src = _plant_malicious(before_src, 799)
    chunks = chunk_source(after_src)
    hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)
    assert hunk_ranges
    hunk_start = hunk_ranges[0][0]
    containing = [
        i for i, ch in enumerate(chunks) if ch.start_byte <= hunk_start < ch.end_byte
    ]
    assert containing, "ROUND-2 BLOCKER #4: diff hunk at func_799 dropped"


def test_chunk_source_under_cap_unchanged():
    """Files under the cap must still produce every chunk (regression guard)."""
    before_src = _make_python_file(200)
    after_src = _plant_malicious(before_src, 100)
    chunks = chunk_source(after_src)
    hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)
    assert hunk_ranges
    hunk_start = hunk_ranges[0][0]
    containing = [
        i for i, ch in enumerate(chunks) if ch.start_byte <= hunk_start < ch.end_byte
    ]
    assert containing, "regression: under-cap files must keep every chunk"
