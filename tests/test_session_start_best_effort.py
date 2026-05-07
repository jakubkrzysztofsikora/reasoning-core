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


def test_unset_emits_no_envelope_to_stdout():
    """Env unset → no stdout envelope. Audit receipt is still written
    (decision=skipped) so a reviewer can prove the hook ran but did not fire."""
    r = _run()
    assert r.returncode == 0
    assert r.stdout == ""
    assert r.stderr == ""


def test_zero_emits_no_envelope():
    r = _run({"RC_BEST_EFFORT_SPEC": "0"})
    assert r.returncode == 0
    assert r.stdout == ""


def test_empty_emits_no_envelope():
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


def test_audit_receipt_on_fire(tmp_path):
    """Reviewer-mandated symmetric receipt: when RC_BEST_EFFORT_SPEC=1, hook
    writes an audit-log line with decision=injected, signal_source=best_effort_spec,
    overlay_sha set. Lets a reviewer prove the lever fired from artifacts alone.

    Uses RC_AUDIT_ROOT to redirect the audit log into tmp_path.
    """
    audit_root = tmp_path / "audit"
    r = _run({
        "RC_BEST_EFFORT_SPEC": "1",
        "RC_AUDIT_ROOT": str(audit_root),
    })
    assert r.returncode == 0
    # Find the JSONL the hook wrote.
    jsonl_files = list(audit_root.rglob("*.jsonl"))
    assert jsonl_files, f"no audit JSONL written under {audit_root}"
    rows = []
    for f in jsonl_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    receipts = [r for r in rows if r.get("signal_source") == "best_effort_spec"]
    assert len(receipts) == 1, f"expected 1 receipt, got {len(receipts)}: {receipts}"
    rec = receipts[0]
    assert rec["decision"] == "injected"
    assert rec["tool_name"] == "SessionStart"
    assert rec.get("overlay_sha"), "overlay_sha must be set on fire path"
    assert "rc_best_effort_spec=1" in rec.get("reason", "")


def test_audit_receipt_on_skip(tmp_path):
    """Symmetric receipt on the skip path: when env unset/=0, hook still
    writes a decision=skipped audit line so reviewer can prove the hook
    ran (vs. unregistered) and the lever was NOT active."""
    audit_root = tmp_path / "audit"
    r = _run({
        "RC_BEST_EFFORT_SPEC": "0",
        "RC_AUDIT_ROOT": str(audit_root),
    })
    assert r.returncode == 0
    jsonl_files = list(audit_root.rglob("*.jsonl"))
    assert jsonl_files, "skip path must still emit audit receipt"
    rows = []
    for f in jsonl_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    receipts = [r for r in rows if r.get("signal_source") == "best_effort_spec"]
    assert len(receipts) == 1
    rec = receipts[0]
    assert rec["decision"] == "skipped"
    assert rec.get("overlay_sha") is None, "overlay_sha must be null on skip"
    assert "rc_best_effort_spec=0" in rec.get("reason", "")
