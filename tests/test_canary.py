"""Tests for the synthetic `rc doctor` evidence canary."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

from src.hooks import _canary as canary  # noqa: E402
from src.hooks import _session_correlator as _sc  # noqa: E402


def test_canary_produces_correlated_synthetic_receipt_and_cleans_up():
    ok, report = canary.run_evidence_canary()

    assert ok is True, report
    assert report["synthetic"] is True
    assert report["receipts"] >= 1
    assert "parse" in report["kinds"]
    assert report["correlation_ok"] is True
    assert report["missing_fields"] == []
    assert report["cleaned_up"] is True
    assert not Path(report["temp_dir"]).exists()


def test_canary_paths_are_recognized_as_synthetic_fixtures():
    assert _sc._is_synthetic_fixture({"project_dir": "/tmp/rc-doctor-canary-x"})
    assert _sc._is_synthetic_fixture({"session_id": "rc-doctor-canary-x"})
    assert not _sc._is_synthetic_fixture({"project_dir": "/repo", "session_id": "s1"})
    assert not _sc._is_synthetic_fixture({
        "project_dir": "/repo/nested-rc-doctor-canary-file",
    })


def test_doctor_reports_evidence_canary(tmp_path, capsys):
    import rc_cli

    rc_cli.main(["doctor", "--project-dir", REPO_ROOT, "--json"])

    payload = json.loads(capsys.readouterr().out)
    canary_check = next(
        item for item in payload["checks"] if item["name"] == "evidence_canary"
    )
    assert canary_check["ok"] is True
    assert "synthetic" in canary_check["detail"]


def test_doctor_survives_missing_canary_module(monkeypatch, capsys):
    import rc_cli

    monkeypatch.setitem(sys.modules, "_canary", None)

    rc_cli.main(["doctor", "--project-dir", REPO_ROOT, "--json"])

    payload = json.loads(capsys.readouterr().out)
    canary_check = next(
        item for item in payload["checks"] if item["name"] == "evidence_canary"
    )
    assert canary_check["ok"] is False
    assert "unavailable" in canary_check["detail"]
