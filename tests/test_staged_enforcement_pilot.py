"""End-to-end pilot test for the staged enforcement profile.

Verifies:
  - Stage 1 (RC_PLAN_GROUNDING=1): edits outside plan warn but don't block.
  - Stage 2 (RC_PLAN_GROUNDING=2): edits outside plan block.
  - Edits inside plan pass.
  - PLAN.md edits always pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = str(REPO_ROOT / "src" / "hooks" / "pre_edit_guard.py")
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")


def _init_repo(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(project_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(project_dir), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(project_dir), check=True, capture_output=True,
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
    # Start from a clean env: strip all RC_/S2_/HOST_ vars that could leak
    # from the parent shell (.envrc, direnv, etc.), then inject only what
    # this test owns.
    STRIP = (
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
    env = {k: v for k, v in os.environ.items() if k not in STRIP}
    env.update(env_extra)
    env["RC_PROJECT_DIR"] = str(project_dir)
    env["RC_RUN_DIR"] = str(project_dir)
    env["RC_AUDIT_ROOT"] = str(project_dir / "_audit")
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _read_audit_events(project_dir: Path) -> list[dict]:
    """Read today's audit JSONL for the project."""
    import datetime as _dt
    import gzip
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
    for f in audit_dir.glob("*.jsonl.gz"):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        events.append(json.loads(line))
        except (OSError, ValueError):
            pass
    return events


def test_stage1_warns_outside_plan(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n- `src/in_plan.py`: 10 lines\n",
        encoding="utf-8",
    )

    in_plan = project / "src" / "in_plan.py"
    in_plan.parent.mkdir()
    in_plan.write_text("# ok\n", encoding="utf-8")

    out_of_plan = project / "src" / "out_of_plan.py"
    out_of_plan.write_text("# original\n", encoding="utf-8")

    proc = _run_hook(
        project,
        "src/out_of_plan.py",
        "# original",
        "# modified\n",
        env_extra={"RC_PLAN_GROUNDING": "1", "RC_MODE": "copilot"},
    )
    # Stage 1 = warn only, exit 0
    assert proc.returncode == 0, proc.stderr
    assert "plan_impl_drift" in proc.stderr or "WARN" in proc.stderr
    events = _read_audit_events(project)
    drift_events = [e for e in events if e.get("reason") == "plan_impl_drift"]
    assert drift_events, "expected a plan_impl_drift audit event"
    assert drift_events[-1]["decision"] == "warn"
    assert drift_events[-1]["signal_source"] in ("plan_grounding", "symbolic_fallback")


def test_stage2_blocks_outside_plan(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n- `src/in_plan.py`: 10 lines\n",
        encoding="utf-8",
    )

    in_plan = project / "src" / "in_plan.py"
    in_plan.parent.mkdir()
    in_plan.write_text("# ok\n", encoding="utf-8")

    out_of_plan = project / "src" / "out_of_plan.py"
    out_of_plan.write_text("# original\n", encoding="utf-8")

    proc = _run_hook(
        project,
        "src/out_of_plan.py",
        "# original",
        "# modified\n",
        env_extra={"RC_PLAN_GROUNDING": "2", "RC_MODE": "copilot"},
    )
    # Stage 2 = hard block, exit 2
    assert proc.returncode == 2, proc.stderr
    assert "plan_impl_drift" in proc.stderr or "BLOCKED" in proc.stderr
    events = _read_audit_events(project)
    drift_events = [e for e in events if e.get("reason") == "plan_impl_drift"]
    assert drift_events, "expected a plan_impl_drift audit event"
    assert drift_events[-1]["decision"] == "blocked"
    assert drift_events[-1]["signal_source"] in ("plan_grounding", "symbolic_fallback")


def test_in_plan_passes(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n- `src/in_plan.py`: 10 lines\n",
        encoding="utf-8",
    )

    in_plan = project / "src" / "in_plan.py"
    in_plan.parent.mkdir()
    in_plan.write_text("# original\n", encoding="utf-8")

    proc = _run_hook(
        project,
        "src/in_plan.py",
        "# original",
        "# modified\n",
        env_extra={"RC_PLAN_GROUNDING": "2", "RC_MODE": "copilot"},
    )
    # In-plan edit should pass (exit 0)
    assert proc.returncode == 0, proc.stderr


def test_plan_md_edits_always_pass(tmp_path):
    project = tmp_path / "repo"
    _init_repo(project)
    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n- `src/old.py`: 10 lines\n",
        encoding="utf-8",
    )

    proc = _run_hook(
        project,
        "PLAN.md",
        "# PLAN\n\n## Phase 1\n- `src/old.py`: 10 lines\n",
        "# PLAN\n\n## Phase 1\n- `src/old.py`: 10 lines\n- `src/new.py`: 5 lines\n",
        env_extra={"RC_PLAN_GROUNDING": "2", "RC_MODE": "copilot"},
    )
    # PLAN.md edits always pass plan-grounding
    assert proc.returncode == 0, proc.stderr
    assert "plan_impl_drift" not in proc.stderr, proc.stderr


def test_stage2_advise_downgrades_to_warn(tmp_path):
    """In advise mode (RC_MODE=advise), Stage 2 should warn not block."""
    project = tmp_path / "repo"
    _init_repo(project)
    plan = project / "PLAN.md"
    plan.write_text(
        "# PLAN\n\n## Phase 1\n- `src/in_plan.py`: 10 lines\n",
        encoding="utf-8",
    )

    (project / "src").mkdir()
    (project / "src" / "out_of_plan.py").write_text("# original\n", encoding="utf-8")

    proc = _run_hook(
        project,
        "src/out_of_plan.py",
        "# original",
        "# modified\n",
        env_extra={"RC_PLAN_GROUNDING": "2", "RC_MODE": "advise"},
    )
    # advise mode downgrades to warn, exit 0
    assert proc.returncode == 0, proc.stderr


def test_missing_plan_md_emits_audit_only(tmp_path):
    """Missing PLAN.md emits an audit_only event, not a block."""
    project = tmp_path / "repo"
    _init_repo(project)
    # No PLAN.md
    (project / "src").mkdir()
    (project / "src" / "anything.py").write_text("# original\n", encoding="utf-8")

    proc = _run_hook(
        project,
        "src/anything.py",
        "# original",
        "# modified\n",
        env_extra={"RC_PLAN_GROUNDING": "2", "RC_MODE": "copilot"},
    )
    # No plan → audit_only, not a block
    assert "plan_impl_drift" not in proc.stderr, proc.stderr
    events = _read_audit_events(project)
    no_plan_events = [e for e in events if e.get("reason") == "no_plan_md"]
    assert no_plan_events, "expected a no_plan_md audit event"
    assert no_plan_events[-1]["decision"] == "audit_only"