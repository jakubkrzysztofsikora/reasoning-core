"""Tests for `rc benchmark` audit-log benchmark command."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import rc_cli  # noqa: E402


def _write_event(day_dir: Path, **fields) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / "anon-bench-test.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(fields) + "\n")


def _today_dir(tmp: Path) -> Path:
    day = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    return tmp / day


def _yesterday_dir(tmp: Path) -> Path:
    day = (_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return tmp / day


def test_empty_audit_root_prints_zero_events(tmp_path, capsys):
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=None,
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Total events" in out
    assert "0" in out


def test_benchmark_populated_from_synthetic_audit(tmp_path, capsys):
    day = _today_dir(tmp_path)
    for _ in range(40):
        _write_event(day, decision="allowed", tool_name="Bash", latency_ms=5, reason="ok")
    for _ in range(5):
        _write_event(
            day,
            decision="blocked",
            tool_name="Edit",
            latency_ms=20,
            reason="contract_violation:import:no_hooks_import_supervisor",
            gate_id="rules",
        )
    for _ in range(3):
        _write_event(day, decision="warn", tool_name="Edit", latency_ms=15, reason="plan_impl_drift")
    for _ in range(2):
        _write_event(
            day,
            decision="blocked",
            tool_name="Edit",
            latency_ms=25,
            reason="plan_impl_drift",
            retry_after_block=True,
        )
    for _ in range(2):
        _write_event(
            day,
            decision="fail-open",
            tool_name="Edit",
            latency_ms=1500,
            reason="sidecar_unavailable:http_503",
            signal_source="timeout",
        )

    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=None,
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "reasoning-core benchmark" in out
    assert "Total events" in out
    assert "contract" in out
    assert "plan_grounding" in out
    assert "Scope-creep catches" in out


def test_benchmark_date_window_filters_events(tmp_path, capsys, monkeypatch):
    today_dir = _today_dir(tmp_path)
    yesterday_dir = _yesterday_dir(tmp_path)
    today_str = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    yesterday_str = (_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(today_dir, ts=today_str, decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    _write_event(yesterday_dir, ts=yesterday_str, decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    # Untimestamped events must be excluded when a date window is requested.
    _write_event(today_dir, decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")

    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=_dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d"),
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Total events" in out
    for line in out.splitlines():
        if line.startswith("| Total events"):
            assert "1" in line, f"expected 1 event after filter, got: {line}"


def test_benchmark_override_survival_no_git_repo(tmp_path, capsys, monkeypatch):
    """Benchmark must not crash when run outside a git repo with override events."""
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path))
    # Force a non-git cwd for the subprocess git rev-parse call.
    monkeypatch.chdir(tmp_path)
    day = _today_dir(tmp_path)
    _write_event(
        day,
        ts=_dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        decision="allowed_via_override",
        tool_name="Edit",
        file_path="src/foo.py",
        reason="kill_switch_or_bypass_next",
        extra={"git_head": "abc12345"},
    )
    rc = rc_cli.main(["benchmark", "--days", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Allowed via override" in out
    assert "Total events" in out


def test_benchmark_invalid_date_format_returns_error(tmp_path, capsys):
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before="not-a-date",
        after=None,
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "YYYY-MM-DD" in err


def test_benchmark_json_output(tmp_path):
    day = _today_dir(tmp_path)
    _write_event(day, decision="blocked", tool_name="Edit", latency_ms=20, reason="rule_engine:no_shell_true")
    json_path = tmp_path / "bench.json"

    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=None,
        output=None,
        json=str(json_path),
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    assert json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["total_events"] == 1
    assert data["blocked"] == 1
    assert data["severity"]["rule_engine"] == 1


def test_benchmark_markdown_output_file(tmp_path):
    day = _today_dir(tmp_path)
    _write_event(day, decision="blocked", tool_name="Edit", latency_ms=20, reason="plan_impl_drift")
    out_path = tmp_path / "bench.md"

    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=None,
        output=str(out_path),
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")
    assert "reasoning-core benchmark" in text


def test_benchmark_before_only_warns_about_days_bound(tmp_path, capsys):
    """A --before-only window is silently truncated by the default --days."""
    day = _today_dir(tmp_path)
    _write_event(day, ts="2026-01-01T00:00:00Z", decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before="2026-06-01",
        after=None,
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "bounded by --days" in err


def test_benchmark_non_string_ts_with_window_is_skipped(tmp_path, capsys):
    """Numeric or missing timestamps must not crash date-filtered runs."""
    day = _today_dir(tmp_path)
    _write_event(day, ts=1720000000, decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    _write_event(day, ts="2026-01-01T00:00:00Z", decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=365,
        before=None,
        after="2026-01-01",
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.startswith("| Total events"):
            assert "1" in line, f"expected 1 event, got: {line}"


def test_benchmark_override_survival_skipped_for_historical_window(tmp_path, capsys):
    """Override survival compares against the current tree and is skipped for windows."""
    day = _today_dir(tmp_path)
    _write_event(
        day,
        ts=_dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        decision="allowed_via_override",
        tool_name="Edit",
        file_path="src/foo.py",
        reason="kill_switch_or_bypass_next",
        extra={"git_head": "abc12345"},
    )
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=_dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d"),
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Override survival" not in out


def test_benchmark_severity_table_excludes_allowed_events(tmp_path, capsys):
    """The 'Events by class' table must not be dominated by allowed events."""
    day = _today_dir(tmp_path)
    for _ in range(10):
        _write_event(day, decision="allowed", tool_name="Bash", latency_ms=5, reason="ok")
    _write_event(day, decision="blocked", tool_name="Edit", latency_ms=10, reason="plan_impl_drift")
    args = argparse.Namespace(
        audit_root=str(tmp_path),
        days=30,
        before=None,
        after=None,
        output=None,
        json=None,
    )
    rc = rc_cli.cmd_benchmark(args)
    assert rc == 0
    out = capsys.readouterr().out
    # The class table should not contain an "other" row inflated by allowed events.
    class_section = out.split("## Events by class")[-1]
    assert "| other" not in class_section
    assert "| plan_grounding" in class_section


def test_benchmark_main_via_argparse(tmp_path, capsys):
    day = _today_dir(tmp_path)
    _write_event(day, decision="blocked", tool_name="Edit", latency_ms=20, reason="plan_impl_drift")
    rc = rc_cli.main(["benchmark", "--audit-root", str(tmp_path), "--days", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reasoning-core benchmark" in out
