from __future__ import annotations

import json
import os
import subprocess
import sys

from eval.timing_pilot import run


def test_timing_pilot_fixture_runs_with_symbolic_fallback(tmp_path, monkeypatch):
    """The harness records outcomes even when a live sidecar is unavailable."""
    out_dir = tmp_path / "out"
    monkeypatch.setenv("S2_URL", "http://127.0.0.1:9")
    result = run.main(["--repeats", "1", "--allow-symbolic-fallback", "--out-dir", str(out_dir)])
    assert result == 0
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["kind"] == "deterministic_feedback_timing_mechanism_study"
    assert report["symbolic_fallback"] is True
    assert report["summary"]["all_treatment_calls_scored"] is False
    assert report["summary"]["violations_caught"] == report["summary"]["violations"]
    assert report["summary"]["false_blocks"] == 0
    assert len(report["probes"]) == 7
    assert (out_dir / "raw.jsonl").read_text(encoding="utf-8").count("\n") == 7


def test_policy_control_fails_for_fixture_contract(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    probe = next(p for p in run._load_probes(run.DEFAULT_PROBES) if p["id"] == "contract-forbidden-path")
    run._write_fixture(project, probe)
    proc = subprocess.run(
        [sys.executable, "-m", "eval.timing_pilot.check_policy", "--project", ".", "--path", probe["path"]],
        cwd=project,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (
            str(run.ROOT), os.environ.get("PYTHONPATH"))))},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "contract violation" in proc.stderr
