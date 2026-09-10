"""Tests for session-start correlation receipts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "src" / "hooks" / "session_start_manifest.py"


def test_session_start_emits_correlated_receipt(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    audit_root = tmp_path / "events"
    env = os.environ.copy()
    env.update(
        {
            "RC_AUDIT_ROOT": str(audit_root),
            "RC_STATE_DIR": str(tmp_path / "state"),
            "RC_PROJECT_DIR": str(project),
            "RC_SESSION_ID": "session-start-test",
            "RC_LANG_LOCK": "0",
        }
    )
    payload = {
        "cwd": str(project),
        "run_id": "run-start",
        "task_id": "task-start",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_call_id": "session-tool",
        "turn_index": 3,
        "baseline_id": "baseline-start",
    }

    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )

    assert proc.returncode == 0, proc.stderr
    files = sorted(audit_root.glob("*/*.jsonl"))
    assert files
    event = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "session_started"
    assert event["run_id"] == "run-start"
    assert event["task_id"] == "task-start"
    assert event["transcript_path"].endswith("transcript.jsonl")
    assert event["tool_call_id"] == "session-tool"
    assert event["turn_index"] == 3
    assert event["baseline_id"] == "baseline-start"
    assert event["language_lock_enabled"] is False
