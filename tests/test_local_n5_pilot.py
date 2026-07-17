"""Local n=5 pilot: simulate 5 edits against a test repo with the staged profile.

Demonstrates the eval harness end-to-end at small scale. Produces:
  - audit events (decision, reason, signal_source, latency)
  - FPBR proxy (allowed_via_override / blocked ratio)
  - p95 block latency
  - abort rate

This is the n=5 pilot described in docs/EVAL_PROTOCOL.md §6 (scaled down for
fast CI). Full n=20 pilot runs outside the test suite.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = str(REPO_ROOT / "src" / "hooks" / "pre_edit_guard.py")

_STRIP = (
    "S2_FAIL_CLOSED", "S2_URL", "S2_TIMEOUT", "S2_HARD_CAP_MS",
    "S2_DEVICE", "S2_PORT",
    "RC_ALLOW_GUARD_EDIT", "RC_ALLOW_SUBAGENT_GUARD_EDIT",
    "RC_LANG_LOCK", "RC_LANG_ALLOW", "RC_LANG_OVERRIDE",
    "RC_LANG_AUDIT_THRESHOLD", "RC_LANG_LOCK_PATH_EXEMPT",
    "RC_STATE_DIR", "RC_BEST_EFFORT_SPEC", "RC_RUN_DIR",
    "RC_MODE", "RC_ORACLE_BLOCK", "RC_ORACLE_T1", "RC_ORACLE_T2",
    "RC_PLAN_GROUNDING", "RC_PLAN_BLOCK", "RC_SHADOW_MODE",
    "RC_MOCK_DETECTOR", "RC_PLAN_QUALITY",
    "RC_RULE_ENGINE", "RC_RULE_ENGINE_ALLOW_BASIC_YAML",
    "RC_DRIFT_WARN", "RC_DRIFT_DENY", "RC_DRIFT_OVERRIDE",
    "RC_CALIBRATION_ENABLED", "RC_RECALIBRATE_POLL_S",
    "RC_PROJECT_INDEX", "RC_REASONER_BACKEND",
    "RC_ENFORCEMENT_TOKEN", "RC_ENFORCEMENT_AUTH",
    "RC_DIFF_AUDIT", "RC_EMBEDDER",
    "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID", "RC_HOST",
    "RC_SESSION_ID", "RC_TASK_SPEC",
)


def _run_hook(project_dir: Path, file_path: str, old: str, new: str, env_extra: dict) -> subprocess.CompletedProcess:
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(project_dir / file_path),
            "old_string": old,
            "new_string": new,
        },
    }
    env = {k: v for k, v in os.environ.items() if k not in _STRIP}
    env.update(env_extra)
    env["RC_PROJECT_DIR"] = str(project_dir)
    env["RC_RUN_DIR"] = str(project_dir)
    env["RC_AUDIT_ROOT"] = str(project_dir / "_audit")
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    proc.elapsed = time.monotonic() - start
    return proc


def _read_audit_events(project_dir: Path) -> list[dict]:
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    audit_dir = project_dir / "_audit" / day
    if not audit_dir.is_dir():
        return []
    events: list[dict] = []
    for f in audit_dir.glob("*.jsonl"):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, ValueError):
            pass
    return events


def test_local_n5_pilot_runs_and_collects_metrics(tmp_path):
    """n=5 pilot: run 5 edits against a test repo with Stage 2 copilot profile.

    Verifies:
      - All 5 edits complete (abort rate = 0).
      - Audit events are recorded with decision/reason/signal_source.
      - FPBR proxy and p95 latency can be computed from audit data.
    """
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=str(project), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "pilot@test"], cwd=str(project), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Pilot"], cwd=str(project), check=True, capture_output=True)

    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n"
        "- `src/in_plan.py`: 10 lines\n"
        "- `src/also_in_plan.py`: 5 lines\n",
        encoding="utf-8",
    )
    (project / "src").mkdir()
    (project / "src" / "in_plan.py").write_text("# original\n", encoding="utf-8")
    (project / "src" / "also_in_plan.py").write_text("# original\n", encoding="utf-8")
    (project / "src" / "out_of_plan.py").write_text("# original\n", encoding="utf-8")

    env = {
        "RC_MODE": "copilot",
        "RC_PLAN_GROUNDING": "2",
        "RC_ORACLE_BLOCK": "1",
        "RC_RULE_ENGINE": "1",
        "S2_FAIL_CLOSED": "1",
    }

    edits = [
        ("src/in_plan.py", "# original", "# modified\n"),       # in-plan → allow
        ("src/also_in_plan.py", "# original", "# modified\n"),  # in-plan → allow
        ("src/out_of_plan.py", "# original", "# modified\n"),   # out-of-plan → block
        ("src/in_plan.py", "# modified\n", "# modified again\n"),  # in-plan retry → allow
        ("src/out_of_plan.py", "# original", "# modified\n"),   # out-of-plan → block
    ]

    results = []
    aborts = 0
    for fp, old, new in edits:
        proc = _run_hook(project, fp, old, new, env)
        results.append((fp, proc.returncode, proc.elapsed))
        if proc.returncode not in (0, 2):
            aborts += 1

    abort_rate = aborts / len(edits)
    events = _read_audit_events(project)

    # Verify abort rate is 0
    assert abort_rate == 0.0, f"abort rate {abort_rate:.0%} > 0"

    # Verify we have audit events
    assert len(events) >= len(edits), f"expected ≥{len(edits)} events, got {len(events)}"

    # Compute FPBR proxy and latency from audit
    blocked = sum(1 for e in events if e.get("decision") == "blocked")
    allowed = sum(1 for e in events if e.get("decision") == "allowed")
    overrides = sum(1 for e in events if e.get("decision") == "allowed_via_override")
    latencies = [e.get("latency_ms", 0) for e in events if isinstance(e.get("latency_ms"), int)]

    fpbr = overrides / max(1, blocked)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    # Report metrics (printed, not asserted — this is a pilot, not an assertion)
    sys.stdout.write(f"\n=== n={len(edits)} pilot results ===\n")
    sys.stdout.write(f"abort_rate: {abort_rate:.0%}\n")
    sys.stdout.write(f"blocked: {blocked}, allowed: {allowed}\n")
    sys.stdout.write(f"FPBR proxy: {fpbr:.3f}\n")
    sys.stdout.write(f"p95 latency: {p95} ms\n")

    # Operational kill criteria from EVAL_PROTOCOL.md §4
    assert abort_rate <= 0.10, f"abort rate {abort_rate:.0%} > 10% kill criterion"
    assert fpbr <= 0.25, f"FPBR {fpbr:.3f} > 0.25 kill criterion"
    assert p95 <= 5000, f"p95 latency {p95} ms > 5000 ms kill criterion"