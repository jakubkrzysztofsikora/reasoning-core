"""U5: auto-scaffold PLAN.md from README.md when RC_PLAN_GROUNDING is enabled.

Audit 2026-06-01 §B4: with RC_PLAN_GROUNDING default-on, repos lacking
PLAN.md silently degrade to audit_only:no_plan_md. Generate a stub on
first contact.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "hooks"))

import _plan_scaffold as ps  # noqa: E402


def test_scaffold_creates_plan_from_readme(tmp_path, monkeypatch):
    monkeypatch.delenv("RC_NO_PLAN_SCAFFOLD", raising=False)
    (tmp_path / "README.md").write_text(
        "# My Project\n\nThis project does the cool thing.\n",
        encoding="utf-8",
    )
    out = ps.maybe_scaffold_plan(str(tmp_path))
    assert out is not None
    assert out == tmp_path / "PLAN.md"
    body = out.read_text(encoding="utf-8")
    assert "# PLAN" in body
    assert "This project does the cool thing." in body
    assert "Files in scope" in body
    assert "Tasks" in body
    assert "Auto-scaffolded" in body


def test_scaffold_skipped_when_plan_exists(tmp_path):
    (tmp_path / "README.md").write_text("# X\n\nfoo\n", encoding="utf-8")
    plan = tmp_path / "PLAN.md"
    plan.write_text("# existing plan — do not clobber\n", encoding="utf-8")
    out = ps.maybe_scaffold_plan(str(tmp_path))
    assert out is None
    assert plan.read_text(encoding="utf-8") == "# existing plan — do not clobber\n"


def test_scaffold_skipped_when_no_readme_and_not_forced(tmp_path):
    out = ps.maybe_scaffold_plan(str(tmp_path))
    assert out is None
    assert not (tmp_path / "PLAN.md").exists()


def test_scaffold_with_force_creates_placeholder(tmp_path):
    out = ps.maybe_scaffold_plan(str(tmp_path), force=True)
    assert out is not None
    body = out.read_text(encoding="utf-8")
    assert "# PLAN" in body
    assert "TODO: describe goal" in body


def test_scaffold_skipped_when_env_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_NO_PLAN_SCAFFOLD", "1")
    (tmp_path / "README.md").write_text("# X\n\nfoo\n", encoding="utf-8")
    out = ps.maybe_scaffold_plan(str(tmp_path))
    assert out is None
    assert not (tmp_path / "PLAN.md").exists()


def test_extract_goal_strips_badges():
    g = ps._extract_goal(
        "# Project\n\n[![CI](x)](y) The actual goal sentence.\n"
    )
    assert "actual goal sentence" in g
    assert "![CI]" not in g
