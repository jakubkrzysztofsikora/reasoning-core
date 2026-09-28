"""Portable Mamba3 scoring must retain edits near the end of long files."""

from src.s2_core import _mamba3_edit_context


def test_late_edit_is_in_both_contexts():
    prefix = "\n".join(f"unchanged_{i} = {i}" for i in range(200))
    before, after = _mamba3_edit_context(prefix + "\nvalue = 1\n", prefix + "\nvalue = 2\n")
    assert "value = 1" in before
    assert "value = 2" in after
    assert "unchanged_0" not in before


def test_insertion_keeps_nearby_baseline_context():
    before, after = _mamba3_edit_context("a = 1\nb = 2\n", "a = 1\nb = 2\nc = 3\n")
    assert "b = 2" in before
    assert "c = 3" in after
