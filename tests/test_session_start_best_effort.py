"""Tests for ``src/hooks/session_start_best_effort.py`` (iter-3 Phase 2).

Asserts:
  1. ``RC_BEST_EFFORT_SPEC`` unset/0/empty → no stdout, exit 0.
  2. ``RC_BEST_EFFORT_SPEC=1`` → JSON envelope matching the
     ``hookSpecificOutput.hookEventName == "SessionStart"`` schema.
  3. ``additionalContext`` field equals the module's ``_OVERLAY`` constant
     (no drift between code and test fixtures).
  4. Exit code 0 in both branches (hook is non-blocking).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "src" / "hooks" / "session_start_best_effort.py"


def _run(env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Wipe any inherited iter-3 env so each case starts clean.
    env.pop("RC_BEST_EFFORT_SPEC", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_unset_emits_nothing():
    r = _run()
    assert r.returncode == 0
    assert r.stdout == ""
    assert r.stderr == ""


def test_zero_emits_nothing():
    r = _run({"RC_BEST_EFFORT_SPEC": "0"})
    assert r.returncode == 0
    assert r.stdout == ""


def test_empty_emits_nothing():
    r = _run({"RC_BEST_EFFORT_SPEC": ""})
    assert r.returncode == 0
    assert r.stdout == ""


def test_one_emits_json_envelope():
    r = _run({"RC_BEST_EFFORT_SPEC": "1"})
    assert r.returncode == 0
    assert r.stdout, "expected non-empty stdout when RC_BEST_EFFORT_SPEC=1"
    payload = json.loads(r.stdout)
    assert "hookSpecificOutput" in payload
    inner = payload["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert isinstance(inner["additionalContext"], str)
    assert inner["additionalContext"], "additionalContext must be non-empty"


def test_additional_context_matches_module_constant():
    """Lock-step: the overlay text in stdout must equal the module's _OVERLAY.
    Catches drift between code and test fixtures if either side changes."""
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    import session_start_best_effort as ssbe  # type: ignore

    r = _run({"RC_BEST_EFFORT_SPEC": "1"})
    payload = json.loads(r.stdout)
    assert payload["hookSpecificOutput"]["additionalContext"] == ssbe._OVERLAY
