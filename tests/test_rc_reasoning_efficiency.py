"""U7: `rc reasoning-efficiency` audit-log composite metric."""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import rc_cli  # noqa: E402


def _write_event(day_dir: Path, **fields) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / "anon-eff-test.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(fields) + "\n")


def _today_dir(tmp: Path) -> Path:
    day = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    return tmp / day


def test_empty_audit_root_prints_zero_events(tmp_path, capsys):
    args = argparse.Namespace(audit_root=str(tmp_path), days=30)
    rc = rc_cli.cmd_reasoning_efficiency(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no events" in out


def test_metric_populated_from_synthetic_audit(tmp_path, capsys):
    day = _today_dir(tmp_path)
    for _ in range(40):
        _write_event(day, decision="allowed", tool_name="Bash", latency_ms=5, reason="")
    for _ in range(5):
        _write_event(day, decision="blocked", tool_name="Edit", latency_ms=20,
                     reason="plan_impl_drift", retry_after_block=False)
    for _ in range(2):
        _write_event(day, decision="warn", tool_name="Edit", latency_ms=15,
                     reason="plan_impl_drift")
    for _ in range(3):
        _write_event(day, decision="fail-open", tool_name="Edit", latency_ms=1500,
                     reason="sidecar_unavailable:http_503")
    args = argparse.Namespace(audit_root=str(tmp_path), days=30)
    rc = rc_cli.cmd_reasoning_efficiency(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "reasoning_efficiency" in out
    assert "drift_caught" in out
    # drift_caught = 7 (5 blocked + 2 warn)
    assert "drift_caught" in out
    for line in out.splitlines():
        if line.strip().startswith("drift_caught"):
            assert "7" in line


def test_main_via_argparse(tmp_path, capsys):
    day = _today_dir(tmp_path)
    _write_event(day, decision="allowed", tool_name="Bash", latency_ms=2)
    rc = rc_cli.main(["reasoning-efficiency", "--audit-root", str(tmp_path), "--days", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reasoning_efficiency" in out
