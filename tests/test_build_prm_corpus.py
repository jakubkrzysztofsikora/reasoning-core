"""U9: eval/build_prm_corpus.py — auto-labeled PRM training corpus.

Audit 2026-06-01 §9: mine iter-2 Claude sessions into
(plan_claim, diff_hunk, step_label, outcome_label) JSONL rows ready for
PRM fine-tuning, using the AgentPRM outcome-propagation proxy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "prm_corpus_mini"
SCRIPT = REPO_ROOT / "eval" / "build_prm_corpus.py"


def _required_fields():
    return {
        "id", "task_id", "arm", "plan_claim",
        "diff_hunk", "step_label", "outcome_label", "source",
    }


def test_corpus_built_from_fixture(tmp_path):
    out = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--in", str(FIXTURE), "--out", str(out)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert rows, "corpus empty"
    for row in rows:
        assert set(row.keys()) >= _required_fields()
        assert row["step_label"] in (-1, 0, 1)
        assert row["outcome_label"] in (0, 1)


def test_stderr_summary_lists_label_distribution(tmp_path):
    out = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--in", str(FIXTURE), "--out", str(out)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "label distribution" in r.stderr
    # A/T1 resolved → +1 rows; B/T1 regression_introduced w/ src/auth.py → -1 rows.
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    labels = [r["step_label"] for r in rows]
    assert 1 in labels and -1 in labels


def test_limit_caps_output(tmp_path):
    out = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--in", str(FIXTURE), "--out", str(out), "--limit", "1"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    rows = [line for line in out.read_text().splitlines() if line.strip()]
    assert len(rows) == 1


def test_missing_eval_root_is_a_noop(tmp_path):
    out = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--in", str(tmp_path / "does-not-exist"),
         "--out", str(out)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "does not exist" in r.stderr
    assert not out.exists()
