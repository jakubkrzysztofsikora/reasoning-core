"""Tests for src/hooks/pre_plan_guard.py.

Drives the hook via subprocess with a synthetic Write payload on stdin.
Avoids touching the SSM backbone — the hook treats any embed failure as a
``novelty_unavailable`` info advisory, so tests work in environments without
torch/transformers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "src", "hooks", "pre_plan_guard.py")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import pre_plan_guard  # type: ignore  # noqa: E402


def _run(payload, *, env_extra=None, timeout=20):
    env = os.environ.copy()
    env.pop("RC_PLAN_BLOCK", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _plan_payload(content: str, *, path: str = "thoughts/shared/plans/foo.plan.md"):
    return {
        "tool_name": "Write",
        "tool_input": {
            "file_path": path,
            "content": content,
        },
    }


# --- path filter -----------------------------------------------------------

def test_non_plan_path_exits_zero_silently():
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "/Users/x/repo/src/foo.py",
            "content": "# huge file\n" + ("x = 1\n" * 5000),
        },
    }
    r = _run(payload)
    assert r.returncode == 0
    assert r.stderr.strip() == ""


def test_non_write_tool_exits_zero():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "PLAN.md", "new_string": "x"}}
    r = _run(payload)
    assert r.returncode == 0


# --- heuristics ------------------------------------------------------------

def test_balanced_plan_no_warnings():
    content = (
        "# Plan: small refactor\n\n"
        "## Phase 1: introduce helper\n\n"
        "- `src/util/foo.py` — ~80 LOC\n\n"
        "## Phase 2: tests\n\n"
        "- `tests/test_foo.py` — ~60 LOC\n"
    )
    r = _run(_plan_payload(content))
    # Allow novelty_unavailable info-level warning; should never be 'block'.
    assert r.returncode == 0
    if r.stderr.strip():
        # Only info-severity warnings allowed for balanced plan.
        for line in r.stderr.strip().splitlines():
            if line.startswith("{"):
                rec = json.loads(line)
                assert rec.get("severity") in {"info"}


def test_per_file_loc_over_budget_warns():
    content = (
        "# Plan: huge file\n\n"
        "## Phase 1\n\n"
        "- `src/big.py` — ~600 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "per_file_loc" for p in parsed)


def test_per_file_loc_block_severity_exits_two_under_env():
    content = (
        "# Plan: enormous file\n\n"
        "## Phase 1\n\n"
        "- `src/monster.py` — ~1200 LOC\n"
    )
    r = _run(_plan_payload(content), env_extra={"RC_PLAN_BLOCK": "1"})
    assert r.returncode == 2
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("severity") == "block" for p in parsed)


def test_per_file_loc_block_default_exits_zero():
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "- `src/monster.py` — ~1200 LOC\n"
    )
    r = _run(_plan_payload(content))
    # Default (RC_PLAN_BLOCK unset) -> exit 0 even with block-severity warning.
    assert r.returncode == 0


def test_phase_to_file_ratio_warns():
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "## Phase 2\n\n"
        "## Phase 3\n\n"
        "## Phase 4\n\n"
        "## Phase 5\n\n"
        "- `src/a.py` — ~50 LOC\n"
        "- `src/b.py` — ~50 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "phase_file_ratio" for p in parsed)


def test_boundary_crossing_prose_warns():
    content = (
        "# Plan\n\n"
        "We will refactor across every module in one file that touches auth.\n\n"
        "## Phase 1\n\n"
        "- `src/a.py` — ~40 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "boundary_crossing_prose" for p in parsed)


def test_test_file_loc_uses_smaller_budget():
    """Test files have the 200-LOC budget (vs 400 for non-test)."""
    content = (
        "# Plan\n\n"
        "## Phase 1\n\n"
        "- `tests/test_huge.py` — ~250 LOC\n"
    )
    r = _run(_plan_payload(content))
    assert r.returncode == 0
    parsed = [
        json.loads(l) for l in r.stderr.splitlines() if l.startswith("{")
    ]
    assert any(p.get("rule_id") == "per_file_loc" for p in parsed)


def test_plan_md_path_recognized():
    content = "# small plan\n\n- `src/x.py` — ~10 LOC\n"
    payload = _plan_payload(content, path="/some/repo/PLAN.md")
    r = _run(payload)
    assert r.returncode == 0


def test_malformed_payload_exits_zero():
    env = os.environ.copy()
    r = subprocess.run(
        [sys.executable, HOOK_PATH],
        input="not json{",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert r.returncode == 0
