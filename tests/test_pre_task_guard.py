"""Tests for src/hooks/pre_task_guard.py.

Drives the hook via subprocess with a synthetic Claude Code PreToolUse JSON
payload on stdin. Verifies the screen_prompt logic in-process for unit-level
checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(REPO_ROOT, "src", "hooks", "pre_task_guard.py")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import pre_task_guard  # type: ignore  # noqa: E402


# --- screen_prompt unit ---

def test_screen_prompt_no_verbs():
    res = pre_task_guard.screen_prompt("Read the file and report back.")
    assert res["decision"] == "allowed"
    assert res["verbs"] == []


def test_screen_prompt_mutation_verb_outside_guarded():
    res = pre_task_guard.screen_prompt("Refactor src/util/foo.py to use generics.")
    assert res["decision"] == "advisory"
    assert "refactor" in [v.lower() for v in res["verbs"]]
    assert res["guarded_paths"] == []


def test_screen_prompt_mutation_verb_on_guarded_path(monkeypatch):
    # Scrub operator override env vars so the test sees the same posture as
    # an unprivileged session — without this, RC_ALLOW_GUARD_EDIT=1 in the
    # parent shell defeats the assertion.
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    monkeypatch.delenv("RC_ALLOW_SUBAGENT_GUARD_EDIT", raising=False)
    res = pre_task_guard.screen_prompt(
        "Implement edits to src/s2_core.py and write a new test."
    )
    assert res["decision"] == "blocked"
    assert "src/s2_core.py" in res["guarded_paths"]
    assert any(v in {"implement", "edits", "edit", "write"} for v in res["verbs"])


def test_screen_prompt_hard_override(monkeypatch):
    monkeypatch.setenv("RC_ALLOW_GUARD_EDIT", "1")
    res = pre_task_guard.screen_prompt(
        "Edit .claude/settings.json to remove the matcher."
    )
    assert res["decision"] == "advisory"
    assert res["reason"].startswith("override_")


def test_screen_prompt_soft_override(monkeypatch):
    monkeypatch.delenv("RC_ALLOW_GUARD_EDIT", raising=False)
    monkeypatch.setenv("RC_ALLOW_SUBAGENT_GUARD_EDIT", "1")
    res = pre_task_guard.screen_prompt("Modify src/hooks/pre_edit_guard.py.")
    assert res["decision"] == "advisory"
    assert res["reason"] == "override_RC_ALLOW_SUBAGENT_GUARD_EDIT"


# --- subprocess integration ---

def _run(payload, env_extra=None, *, timeout=10):
    env = os.environ.copy()
    # Always reset overrides so test stays deterministic.
    for k in ("RC_ALLOW_GUARD_EDIT", "RC_ALLOW_SUBAGENT_GUARD_EDIT"):
        env.pop(k, None)
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


def test_safe_research_prompt_exits_zero():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "prompt": "Research the codebase and summarize the call graph.",
            "subagent_type": "researcher",
        },
    }
    r = _run(payload)
    assert r.returncode == 0


def test_implement_on_guarded_path_blocks():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "prompt": "implement edits to src/s2_core.py to add /metrics endpoint.",
            "subagent_type": "engineer",
        },
    }
    r = _run(payload)
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr or "blocked" in r.stderr.lower()


def test_review_only_prompt_exits_zero():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "prompt": "Review the auth code and report issues.",
            "subagent_type": "reviewer",
        },
    }
    r = _run(payload)
    assert r.returncode == 0


def test_soft_override_downgrades_block_to_warn():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "prompt": "Edit src/s2_core.py to add metrics.",
            "subagent_type": "engineer",
        },
    }
    r = _run(payload, env_extra={"RC_ALLOW_SUBAGENT_GUARD_EDIT": "1"})
    assert r.returncode == 0
    assert "advisory" in r.stderr.lower() or "task-guard" in r.stderr


def test_malformed_payload_does_not_block():
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


def test_non_task_tool_exits_zero():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.py"}}
    r = _run(payload)
    assert r.returncode == 0
