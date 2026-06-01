"""U6: scripts/risk_vector_correlation.py — empirical dim-redundancy tool."""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "risk_vector_correlation.py")


def _write_events(day_dir: Path, n: int, *, perfectly_correlated: bool = False) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    f = day_dir / "anon-test.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for _ in range(n):
            vec = [rng.random() for _ in range(8)]
            if perfectly_correlated:
                vec[1] = 2.0 * vec[0]
            fh.write(json.dumps({
                "decision": "allowed",
                "tool_name": "Edit",
                "risk_vector": vec,
                "ts": "2026-06-01T12:00:00Z",
            }) + "\n")


def test_correlation_runs_on_synthetic(tmp_path):
    day = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    _write_events(tmp_path / day, 100)
    r = subprocess.run(
        [sys.executable, SCRIPT, "--events-root", str(tmp_path), "--min-events", "50"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "Pearson correlation" in r.stdout
    assert "Redundant pairs" in r.stdout


def test_warns_when_too_few_events(tmp_path):
    day = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    _write_events(tmp_path / day, 10)
    r = subprocess.run(
        [sys.executable, SCRIPT, "--events-root", str(tmp_path), "--min-events", "50"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "warning" in r.stderr.lower()


def test_perfectly_correlated_dims_flagged(tmp_path):
    day = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    _write_events(tmp_path / day, 100, perfectly_correlated=True)
    r = subprocess.run(
        [sys.executable, SCRIPT, "--events-root", str(tmp_path), "--min-events", "50"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "Redundant pairs" in r.stdout
    # dim_0 and dim_1 are perfectly correlated → r ≈ 1.0
    body = r.stdout
    # Find a redundant-pairs line mentioning both dims (or their s2_core labels)
    redundant_section = body.split("## Redundant pairs")[-1]
    assert "1.000" in redundant_section or "+1.000" in redundant_section
