"""Iter-3 wiring smoke — self-contained pre-flight check.

Validates the iter-3 levers wire end-to-end without depending on the eval
framework, eval-setups/, or any external infrastructure. Asserts the four
checks from the iter-3 pre-mortem (sweep round-5 §pre-flight):

  1. B-side: when RC_BEST_EFFORT_SPEC=1, hook produces an `injected` receipt.
  2. A-side: when env unset/=0, hook produces a `skipped` receipt with
     overlay_sha=null (proves hook ran but lever was off).
  3. Plan grounding: when RC_PLAN_GROUNDING=1 and PLAN.md exists, gate fires.
  4. Plan grounding: when PLAN.md missing, gate emits audit_only event so
     missing-PLAN.md is detectable in audit (B3 fix from sweep round-5).

These tests run on every CI push/PR via the existing
.github/workflows/lint-and-test.yml pytest invocation (no extra workflow
needed). The whole suite runs in <2 seconds.

Independence note: this test deliberately does NOT consume the eval team's
settings.local.json or .envrc. It spawns the iter-3 hook scripts with
controlled env+stdin and reads back the audit JSONL. If the eval framework
changes, this smoke still catches reasoning-core-side wiring regressions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK_BEST_EFFORT = _ROOT / "src" / "hooks" / "session_start_best_effort.py"
_HOOK_PRE_EDIT_GUARD = _ROOT / "src" / "hooks" / "pre_edit_guard.py"


def _run_hook(hook_path: Path, env_extra: dict, stdin: str = "{}") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("RC_") and not k.startswith("S2_")}
    env.update({"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")})
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin, capture_output=True, text=True, env=env, timeout=15,
    )


def _read_audit_jsonl(audit_root: Path, signal_source: str) -> list[dict]:
    rows: list[dict] = []
    for f in audit_root.rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("signal_source") == signal_source:
                rows.append(rec)
    return rows


# ---------------------------------------------------------------------------
# Check 1 — Setup B side: env=1 produces injected receipt
# ---------------------------------------------------------------------------

def test_smoke_setup_b_emits_injected_receipt(tmp_path):
    """When RC_BEST_EFFORT_SPEC=1, the SessionStart hook MUST emit a
    JSON envelope to stdout AND write an audit receipt with
    decision=injected, signal_source=best_effort_spec, overlay_sha set.

    Failure mode caught: hook unregistered or RC_REPO unresolvable in
    eval team's environment → zero audit receipts during sweep → silent
    null result on iter-3 measurement.
    """
    audit_root = tmp_path / "audit"
    r = _run_hook(
        _HOOK_BEST_EFFORT,
        {"RC_BEST_EFFORT_SPEC": "1", "RC_AUDIT_ROOT": str(audit_root)},
    )
    assert r.returncode == 0, f"hook exit {r.returncode}; stderr={r.stderr!r}"
    # Stdout envelope (Claude Code consumes this).
    assert r.stdout, "stdout must contain the additionalContext envelope"
    payload = json.loads(r.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    # Audit receipt (operator/aggregator consumes this).
    receipts = _read_audit_jsonl(audit_root, "best_effort_spec")
    assert len(receipts) == 1, f"expected 1 receipt, got {len(receipts)}"
    rec = receipts[0]
    assert rec["decision"] == "injected"
    assert rec["overlay_sha"], "overlay_sha must be set on fire path"
    assert "rc_best_effort_spec=1" in rec.get("reason", "")


# ---------------------------------------------------------------------------
# Check 2 — Setup A side: env unset/0 produces skipped receipt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_value", ["", "0"])
def test_smoke_setup_a_emits_skipped_receipt(tmp_path, env_value):
    """When RC_BEST_EFFORT_SPEC is unset or "0", the hook MUST run AND
    emit a skipped audit receipt. This is the symmetric receipt contract:
    a reviewer comparing Setup A audit logs to Setup B audit logs sees
    decision=skipped vs decision=injected — proving the difference is
    the lever, not "lever fired in B vs hook never ran in A".
    """
    audit_root = tmp_path / "audit"
    extra = {"RC_AUDIT_ROOT": str(audit_root)}
    if env_value:
        extra["RC_BEST_EFFORT_SPEC"] = env_value
    r = _run_hook(_HOOK_BEST_EFFORT, extra)
    assert r.returncode == 0
    # No stdout envelope (lever was off).
    assert r.stdout == "", f"unexpected stdout when lever off: {r.stdout!r}"
    # Symmetric receipt MUST still be present.
    receipts = _read_audit_jsonl(audit_root, "best_effort_spec")
    assert len(receipts) == 1
    rec = receipts[0]
    assert rec["decision"] == "skipped"
    assert rec.get("overlay_sha") is None, "overlay_sha MUST be null on skip"


# ---------------------------------------------------------------------------
# Check 3 — Plan grounding fires on drift (real-PLAN.md case)
# ---------------------------------------------------------------------------

def test_smoke_plan_grounding_fires_on_drift(tmp_path):
    """When RC_PLAN_GROUNDING=1, RC_RUN_DIR points at a dir with PLAN.md,
    and an edit targets a file NOT in PLAN.md, the gate MUST emit a warn
    audit row tagged signal_source=plan_grounding.

    Failure mode caught: gate silently no-ops because PLAN.md path
    resolution is broken or the regex misses real plan paths.
    """
    plan = tmp_path / "PLAN.md"
    plan.write_text("- `src/foo.py` — ~100 LOC\n")
    target = tmp_path / "src" / "drift_file.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")

    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(target),
            "old_string": "x = 1\n",
            "new_string": "x = 2\n",
        },
    }
    audit_root = tmp_path / "audit"
    r = _run_hook(
        _HOOK_PRE_EDIT_GUARD,
        {
            "RC_PLAN_GROUNDING": "1",
            "RC_RUN_DIR": str(tmp_path),
            "RC_AUDIT_ROOT": str(audit_root),
        },
        stdin=json.dumps(payload),
    )
    # Warn mode — exit 0, stderr advisory, audit event.
    assert r.returncode == 0, f"warn must not block; got {r.returncode}; stderr={r.stderr!r}"
    assert "drifts from plan" in r.stderr
    receipts = _read_audit_jsonl(audit_root, "plan_grounding")
    drift_warns = [r for r in receipts if r.get("decision") == "warn"]
    assert len(drift_warns) >= 1, (
        f"expected ≥1 plan_grounding warn audit; got {receipts}"
    )


# ---------------------------------------------------------------------------
# Check 4 — Plan grounding emits audit_only when PLAN.md missing
# ---------------------------------------------------------------------------

def test_smoke_plan_grounding_audit_on_missing_plan(tmp_path):
    """When RC_PLAN_GROUNDING=1 but no PLAN.md exists, the gate MUST
    emit a decision=audit_only event with reason=no_plan_md, NOT silently
    no-op. B3 fix from sweep round-5: aggregator can detect the case
    where the spawner forgot to write PLAN.md (would otherwise be
    indistinguishable from a perfectly-grounded run in audit data).
    """
    target = tmp_path / "src" / "anything.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n")
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(target),
            "old_string": "x = 1\n",
            "new_string": "x = 2\n",
        },
    }
    audit_root = tmp_path / "audit"
    r = _run_hook(
        _HOOK_PRE_EDIT_GUARD,
        {
            "RC_PLAN_GROUNDING": "1",
            "RC_RUN_DIR": str(tmp_path),  # no PLAN.md inside
            "RC_AUDIT_ROOT": str(audit_root),
        },
        stdin=json.dumps(payload),
    )
    assert r.returncode == 0, "missing PLAN.md must not block"
    receipts = _read_audit_jsonl(audit_root, "plan_grounding")
    audit_only = [r for r in receipts if r.get("decision") == "audit_only"]
    assert len(audit_only) >= 1, (
        f"expected ≥1 audit_only event for missing PLAN.md; got {receipts}"
    )
    assert audit_only[0].get("reason") == "no_plan_md"


# ---------------------------------------------------------------------------
# Check 5 (informational) — Docker probe
# ---------------------------------------------------------------------------

def test_smoke_docker_probe_skip_if_absent():
    """Informational only — does NOT fail CI. Records whether docker is
    present so iter-3 readiness reports can flag T1/E1 will be Docker-blind
    on this machine. Adopters / eval team see the same probe in
    scripts/iter3-preflight.sh."""
    has_docker = subprocess.run(
        ["bash", "-c", "command -v docker && docker ps >/dev/null 2>&1"],
        capture_output=True,
    ).returncode == 0
    if not has_docker:
        pytest.skip(
            "docker not available — T1/E1 will be Docker-blind in this env. "
            "iter-3 cross-system tasks need Docker to clear correctness gate."
        )
    assert has_docker  # if probe says yes, lock the contract
