"""Plan-guard decomposition recipe (research 2026-06-02 §6, Option B).

When a plan's per-file LOC entry exceeds RC_PLAN_LOC_BLOCK (default 1200),
the gate still exits 2 — but stderr now carries an agent-actionable
decomposition recipe instead of just "BLOCK".
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
from contextlib import redirect_stderr
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "hooks"))


@pytest.fixture
def ppg(monkeypatch):
    """Reload the module under test so env overrides are honored fresh."""
    monkeypatch.delenv("RC_PLAN_LOC_BLOCK", raising=False)
    monkeypatch.delenv("RC_PLAN_DECOMPOSE", raising=False)
    monkeypatch.delenv("RC_PLAN_BLOCK", raising=False)
    monkeypatch.delenv("RC_SHADOW_MODE", raising=False)
    if "pre_plan_guard" in sys.modules:
        return importlib.reload(sys.modules["pre_plan_guard"])
    return importlib.import_module("pre_plan_guard")


def test_default_loc_budget_block_is_1200(ppg):
    assert ppg._loc_budget_block() == 1200


def test_env_override_loc_budget(monkeypatch, ppg):
    monkeypatch.setenv("RC_PLAN_LOC_BLOCK", "2500")
    assert ppg._loc_budget_block() == 2500


def test_invalid_env_falls_back_to_default(monkeypatch, ppg):
    monkeypatch.setenv("RC_PLAN_LOC_BLOCK", "not-a-number")
    assert ppg._loc_budget_block() == 1200


def test_recipe_lists_phases_per_file(ppg):
    blocking = [
        {
            "rule_id": "per_file_loc_block",
            "severity": "block",
            "file_path": "src/foo.py",
            "loc": 2500,
            "message": "Estimated LOC 2500 exceeds hard block budget 1200",
        }
    ]
    recipe = ppg._format_decompose_recipe(blocking)
    assert "DECOMPOSITION REQUIRED" in recipe
    assert "src/foo.py" in recipe
    assert "2500 LOC" in recipe
    # 2500 // 1200 + 1 = 3 phases
    assert "3 sequential phases" in recipe
    assert "≤ 1200 LOC" in recipe
    assert "RC_PLAN_LOC_BLOCK" in recipe  # mentions operator override


def test_recipe_multiple_files(ppg):
    blocking = [
        {"rule_id": "per_file_loc_block", "severity": "block",
         "file_path": "a.py", "loc": 1500, "message": "Estimated LOC 1500"},
        {"rule_id": "per_file_loc_block", "severity": "block",
         "file_path": "b.py", "loc": 5000, "message": "Estimated LOC 5000"},
    ]
    recipe = ppg._format_decompose_recipe(blocking)
    assert "a.py" in recipe and "b.py" in recipe
    # b.py: 5000 // 1200 + 1 = 5 phases
    assert "5 sequential phases" in recipe


def test_recipe_silenced_when_env_off(monkeypatch, ppg):
    monkeypatch.setenv("RC_PLAN_DECOMPOSE", "0")
    blocking = [
        {"rule_id": "per_file_loc_block", "severity": "block",
         "file_path": "src/foo.py", "loc": 2500, "message": "x"},
    ]
    assert ppg._format_decompose_recipe(blocking) == ""


def test_recipe_empty_when_no_loc_block(ppg):
    """Other block kinds (novelty/framework_pivot) don't emit the recipe."""
    blocking = [
        {"rule_id": "framework_pivot_in_plan", "severity": "block",
         "file_path": None, "message": "x"},
    ]
    assert ppg._format_decompose_recipe(blocking) == ""


def test_recipe_extracts_loc_from_message_when_field_missing(ppg):
    """Back-compat: older warning dicts may not carry the 'loc' int."""
    blocking = [
        {"rule_id": "per_file_loc_block", "severity": "block",
         "file_path": "src/foo.py", "message": "Estimated LOC 1800 exceeds"},
    ]
    recipe = ppg._format_decompose_recipe(blocking)
    assert "1800 LOC" in recipe


def test_per_file_loc_emits_block_above_new_threshold(ppg, monkeypatch):
    """Confirm the new 1200 budget catches a plan that lists 1500 LOC."""
    monkeypatch.setenv("RC_PLAN_LOC_BLOCK", "1200")
    content = "## Files in scope\n- src/foo.py (~1500 LOC)\n"
    warnings = ppg._check_per_file_loc(content)
    blocking = [w for w in warnings if w.get("severity") == "block"]
    assert blocking, f"expected block but got {warnings}"
    assert blocking[0]["file_path"] == "src/foo.py"
    assert blocking[0].get("loc") == 1500


def test_per_file_loc_no_block_below_threshold(ppg, monkeypatch):
    """LOC just under the new budget no longer blocks (was 800, now 1200)."""
    monkeypatch.setenv("RC_PLAN_LOC_BLOCK", "1200")
    content = "## Files in scope\n- src/foo.py (~1000 LOC)\n"
    warnings = ppg._check_per_file_loc(content)
    blocking = [w for w in warnings if w.get("severity") == "block"]
    assert not blocking, f"unexpected block: {blocking}"
