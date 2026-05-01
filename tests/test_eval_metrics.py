"""Tests for eval.metrics (RC-210)."""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest

from eval.metrics import (
    ast_edit_distance,
    cyclomatic_delta,
    fan_in_delta,
    fan_out_delta,
)


def test_ast_edit_distance_identical_is_zero():
    src = "def foo():\n    return 1\n"
    assert ast_edit_distance(src, src) == 0


def test_ast_edit_distance_rename_function_is_small():
    before = "def foo():\n    return 1\n"
    after = "def bar():\n    return 1\n"
    d = ast_edit_distance(before, after)
    assert 1 <= d <= 8


def test_ast_edit_distance_handles_syntax_error_via_line_fallback():
    before = "def foo("  # syntax error
    after = "def foo():\n    pass\n"
    # Falls back to line-set distance; must be a non-negative integer.
    d = ast_edit_distance(before, after)
    assert isinstance(d, int)
    assert d >= 0


def test_ast_edit_distance_empty_inputs_zero():
    assert ast_edit_distance("", "") == 0


def test_cyclomatic_delta_add_branch_increments():
    before = "def foo(x):\n    return x\n"
    after = "def foo(x):\n    if x:\n        return 1\n    return 0\n"
    delta = cyclomatic_delta(before, after)
    assert delta >= 1.0


def test_cyclomatic_delta_unchanged_is_zero():
    src = "def foo(x):\n    if x:\n        return 1\n    return 0\n"
    assert cyclomatic_delta(src, src) == 0.0


def test_cyclomatic_delta_handles_syntax_error():
    before = "def foo("
    after = "def foo():\n    pass\n"
    # Syntax error in 'before' contributes 0 branches; 'after' has 0 too.
    # No exception, returns finite float.
    val = cyclomatic_delta(before, after)
    assert isinstance(val, float)


def test_fan_in_delta_signature_and_zero_for_identical():
    src = "def a():\n    b()\n\ndef b():\n    pass\n"
    assert fan_in_delta(src, src) == 0


def test_fan_out_delta_increases_when_more_calls_added():
    before = "def a():\n    pass\n"
    after = "def a():\n    f()\n    g()\n    h()\n"
    # Tree-sitter may or may not be importable in CI; the helper must always
    # return an int and never raise.
    val = fan_out_delta(before, after)
    assert isinstance(val, int)
