"""Tests for ``src/hooks/_plan_paths.py`` (iter-3 helper extraction).

Asserts:
  1. Annotated bulleted ``path:LOC`` lines extracted as ``(path, loc)``.
  2. Bare backticked paths extracted.
  3. ``distinct_file_paths`` returns ``set[str]``.
  4. Parity: ``len(distinct_file_paths(c)) == _count_distinct_file_paths(c)``
     for any plan body — single source of truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))

import _plan_paths  # type: ignore  # noqa: E402
import pre_plan_guard  # type: ignore  # noqa: E402


PLAN_WITH_LOC = """
# Plan: foo

- `src/foo.py` — ~120 LOC
- src/bar.ts: ~80 lines
- `tests/test_foo.py` — 45 LOC
"""

PLAN_WITH_BARE = """
The implementation lives in `src/baz.py` and the tests in `tests/test_baz.py`.
See also `docs/README.md` for context.
"""

PLAN_MIXED = PLAN_WITH_LOC + "\n\n" + PLAN_WITH_BARE


def test_extract_files_with_loc_returns_tuples():
    out = _plan_paths.extract_files_with_loc(PLAN_WITH_LOC)
    paths = {p for p, _ in out}
    assert "src/foo.py" in paths
    assert "src/bar.ts" in paths
    assert "tests/test_foo.py" in paths


def test_extract_files_with_loc_skips_bare_paths():
    """Bare backticked paths without LOC annotation are not picked up here —
    that's the bare-path regex's job."""
    out = _plan_paths.extract_files_with_loc(PLAN_WITH_BARE)
    assert out == [], f"expected empty, got {out}"


def test_distinct_file_paths_returns_set():
    paths = _plan_paths.distinct_file_paths(PLAN_MIXED)
    assert isinstance(paths, set)
    # Bulleted-with-LOC paths
    assert "src/foo.py" in paths
    assert "src/bar.ts" in paths
    assert "tests/test_foo.py" in paths
    # Bare backticked paths
    assert "src/baz.py" in paths
    assert "tests/test_baz.py" in paths
    assert "docs/README.md" in paths


def test_parity_with_count_distinct_file_paths():
    """Refactor invariant: count via len(set) == legacy int return."""
    for content in (PLAN_WITH_LOC, PLAN_WITH_BARE, PLAN_MIXED, "", "no paths here"):
        assert len(_plan_paths.distinct_file_paths(content)) == \
            pre_plan_guard._count_distinct_file_paths(content), \
            f"parity broken for content={content!r}"


def test_empty_content():
    assert _plan_paths.distinct_file_paths("") == set()
    assert _plan_paths.extract_files_with_loc("") == []


def test_property_idempotent_on_repeated_extraction():
    """Property: distinct_file_paths is idempotent — running it twice on
    the same content yields the same set. Catches stateful regex regressions."""
    samples = [
        "",
        "no paths",
        "- `src/foo.py` — ~10 LOC",
        "Mixed `src/a.py` and `src/b.ts` text.",
        PLAN_MIXED,
        # Pathological: same path mentioned 50 times (set dedup must hold)
        "\n".join(["- `src/dup.py` — 1 LOC"] * 50),
    ]
    for content in samples:
        first = _plan_paths.distinct_file_paths(content)
        second = _plan_paths.distinct_file_paths(content)
        assert first == second, f"non-idempotent on {content[:40]!r}"


def test_property_set_invariant_under_concatenation():
    """Property: distinct_file_paths(a + b) ⊇ distinct_file_paths(a).
    Adding more content cannot remove paths that were already found."""
    base = "- `src/foo.py` — 10 LOC\n- `src/bar.ts` — 20 LOC"
    additions = [
        "",
        "\n# extra heading\n",
        "\nMentions `src/baz.py` in prose.\n",
        "\n- `tests/x.py` — 5 LOC\n",
    ]
    base_set = _plan_paths.distinct_file_paths(base)
    for add in additions:
        combined = _plan_paths.distinct_file_paths(base + add)
        assert base_set <= combined, (
            f"adding content lost a path: base={base_set} combined={combined}"
        )


def test_property_no_paths_in_pure_prose():
    """Property: text with no backticks and no bullets returns empty set."""
    prose = (
        "This is a plan written entirely in prose without any code references "
        "or file paths whatsoever. Lorem ipsum dolor sit amet."
    )
    assert _plan_paths.distinct_file_paths(prose) == set()


def test_dedup_when_path_appears_in_both_forms():
    """Reviewer-flagged contract: same path hit by both bulleted-LOC AND
    bare-backticked extractors collapses to one entry. set semantics carry
    this implicitly; assert cardinality explicitly so a list-returning
    regression cannot pass."""
    # `src/foo.py` appears as bulleted-with-LOC (line 1) AND as bare-backticked (line 4).
    content = (
        "- `src/foo.py` — ~120 LOC\n"
        "- `src/bar.ts` — ~40 LOC\n"
        "\n"
        "Implementation lives in `src/foo.py` and helpers.\n"
    )
    paths = _plan_paths.distinct_file_paths(content)
    assert paths == {"src/foo.py", "src/bar.ts"}
    assert len(paths) == 2  # explicit cardinality lock
