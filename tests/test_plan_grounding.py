"""Tests for ``_dispatch.gate_plan_grounding`` (iter-3 Phase 3).

Asserts the gate's decision matrix:
  - Mode unset / "0" / non-numeric → action="pass" (disabled).
  - Mode "1" + drift  → action="stderr_only" + signal_source="plan_grounding".
  - Mode "2" + drift  → action="exit_block" + code=2.
  - PLAN.md itself always passes.
  - No PLAN.md exists → pass with reason="no_plan_md".
  - Suffix-match correctness on real plan vocabulary.

Plus the orchestrator-side integration test (RC_PLAN_GROUNDING=2 end-to-end
through pre_edit_guard.py) lives in test_hook_block.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))

import _dispatch  # type: ignore  # noqa: E402


_PLAN_BODY = """
# Plan: foo

- `src/foo.py` — ~120 LOC
- `tests/test_foo.py` — ~50 LOC

The implementation lives in `src/helpers/util.py`.
"""


def _seed_plan(tmp_path: Path) -> Path:
    plan = tmp_path / "PLAN.md"
    plan.write_text(_PLAN_BODY)
    return plan


def test_disabled_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("RC_PLAN_GROUNDING", raising=False)
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/random_other_file.py")
    assert out.action == "pass"
    assert out.decision == ""


def test_disabled_when_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "0")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/random_other_file.py")
    assert out.action == "pass"


def test_disabled_when_garbage(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "yes")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/random_other_file.py")
    assert out.action == "pass"


def test_warn_when_file_not_in_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/random_other_file.py")
    assert out.action == "stderr_only"
    assert out.decision == "warn"
    assert out.reason == "plan_impl_drift"
    assert out.signal_source == "plan_grounding"
    assert "drifts from plan" in out.stderr
    assert out.audit_extra.get("plan_refs_count", 0) >= 3


def test_pass_when_file_in_plan_via_suffix(monkeypatch, tmp_path):
    """Suffix match: full repo-relative path ending with the bare ref string passes."""
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    # Plan refs `src/foo.py`. Real edit path may include an absolute-ish prefix.
    out = _dispatch.gate_plan_grounding(file_path="/abs/repo/src/foo.py")
    assert out.action == "pass"
    assert out.reason == "in_plan"


def test_pass_when_exact_ref(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="tests/test_foo.py")
    assert out.action == "pass"


def test_block_at_level_2(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/unrelated.py")
    assert out.action == "exit_block"
    assert out.code == 2
    assert out.decision == "blocked"
    assert out.signal_source == "plan_grounding"
    assert "plan_impl_drift" in out.reason
    assert "RC_PLAN_GROUNDING=1 for warn-only" in out.stderr


def test_plan_md_itself_always_passes(monkeypatch, tmp_path):
    """Gate MUST NOT block PLAN.md edits — that would prevent plan revision."""
    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="thoughts/shared/plans/foo.plan.md")
    assert out.action == "pass"
    out2 = _dispatch.gate_plan_grounding(file_path="some/dir/PLAN.md")
    assert out2.action == "pass"


def test_no_plan_md_falls_through(monkeypatch, tmp_path):
    """No PLAN.md anywhere → pass with reason=no_plan_md (not a block)."""
    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    # No PLAN.md in tmp_path; gate must NOT block.
    out = _dispatch.gate_plan_grounding(file_path="src/anything.py")
    assert out.action == "pass"
    assert out.reason == "no_plan_md"


def test_empty_file_path_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="")
    assert out.action == "pass"


def test_plan_unreadable_falls_through(monkeypatch, tmp_path):
    """OSError on PLAN.md read → reason='plan_unreadable', no block.

    Boundary: file exists but cannot be opened (permissions / symlink loop).
    Gate must NOT crash and must NOT block — fall-open is the safe default."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(_PLAN_BODY)
    plan.chmod(0o000)  # render unreadable
    try:
        monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
        monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
        out = _dispatch.gate_plan_grounding(file_path="src/anything.py")
        assert out.action == "pass", \
            f"unreadable PLAN.md must not block; got {out.action}"
        assert out.reason == "plan_unreadable"
    finally:
        plan.chmod(0o644)  # restore for tmp cleanup


def test_path_traversal_normalized(monkeypatch, tmp_path):
    """os.path.normpath collapses ``..`` segments before suffix-match.
    Locks contract: agent cannot evade the gate via path traversal."""
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    # Normalized form of `subdir/../src/foo.py` is `src/foo.py` — in plan.
    out = _dispatch.gate_plan_grounding(file_path="subdir/../src/foo.py")
    assert out.action == "pass"
    # Normalized `a/b/../../src/random.py` → `src/random.py`, NOT in plan.
    out2 = _dispatch.gate_plan_grounding(file_path="a/b/../../src/random.py")
    assert out2.action == "stderr_only"


def test_empty_plan_warns_on_every_edit(monkeypatch, tmp_path):
    """Plan with zero file refs → every edit drifts (no refs to match against).
    Documents the boundary: an empty PLAN.md is operator error, not a no-op."""
    (tmp_path / "PLAN.md").write_text("# Plan: empty\n\nNo file references here.\n")
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    out = _dispatch.gate_plan_grounding(file_path="src/anything.py")
    assert out.action == "stderr_only"
    assert out.audit_extra.get("plan_refs_count") == 0


def test_audit_extra_reserved_keys_filtered():
    """Contract: _emit_audit's `extra` kwarg filters out reserved keys to
    prevent TypeError on the **kwargs splat. Engineer-flagged collision risk
    in phase-validation review.

    Direct unit test on the filter constant; integration covered by the
    subprocess tests in test_hook_block.py.
    """
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    import pre_edit_guard as peg  # type: ignore

    reserved = peg._AUDIT_RESERVED_KEYS
    assert "reason" in reserved
    assert "signal_source" in reserved
    assert "decision" in reserved
    # New gate-specific fields are NOT reserved (correctly pass through)
    assert "plan_path" not in reserved
    assert "plan_refs_count" not in reserved


def test_audit_extra_populated(monkeypatch, tmp_path):
    """Contract: audit_extra always contains plan_path + plan_refs_count
    on a fired decision (warn or block). Locks the iter-3 audit signal."""
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    _seed_plan(tmp_path)
    out = _dispatch.gate_plan_grounding(file_path="src/random_other_file.py")
    assert out.action == "stderr_only"
    assert "plan_path" in out.audit_extra
    assert "plan_refs_count" in out.audit_extra
    assert out.audit_extra["plan_refs_count"] >= 3


def test_plan_resolution_precedence(monkeypatch, tmp_path):
    """RC_RUN_DIR wins over CLAUDE_PROJECT_DIR; both win over cwd."""
    run_dir = tmp_path / "run"
    project_dir = tmp_path / "project"
    run_dir.mkdir()
    project_dir.mkdir()
    # Plan in run_dir mentions src/foo.py; plan in project_dir does NOT.
    (run_dir / "PLAN.md").write_text("- `src/foo.py` — 10 LOC\n")
    (project_dir / "PLAN.md").write_text("- `src/other.py` — 10 LOC\n")
    monkeypatch.setenv("RC_PLAN_GROUNDING", "1")
    monkeypatch.setenv("RC_RUN_DIR", str(run_dir))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    out = _dispatch.gate_plan_grounding(file_path="src/foo.py")
    assert out.action == "pass"  # RC_RUN_DIR plan wins
    out2 = _dispatch.gate_plan_grounding(file_path="src/other.py")
    assert out2.action == "stderr_only"  # only in CLAUDE_PROJECT_DIR plan, ignored
