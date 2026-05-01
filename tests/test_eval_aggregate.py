"""Tests for eval/aggregate.py (RC-208).

Uses fixture per-task JSON files; never invokes Claude or a network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest

from eval.aggregate import METRIC_KEYS, _render_markdown, aggregate


def _write_pair(run_dir: Path, task_id: str, vanilla: dict, treatment: dict) -> None:
    base = {
        "task_id": task_id,
        "repo": "fakeorg/fake",
        "base_commit": "deadbeef",
    }
    v = {**base, "arm": "vanilla", **vanilla}
    t = {**base, "arm": "treatment", **treatment}
    (run_dir / f"{task_id}.vanilla.json").write_text(json.dumps(v))
    (run_dir / f"{task_id}.treatment.json").write_text(json.dumps(t))


def test_aggregate_emits_report_schema(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    for i in range(3):
        _write_pair(
            run_dir,
            f"t{i}",
            vanilla={"resolved": False, "regression_introduced": True, "wall_clock_s": 100, "tokens_in": 1000, "tokens_out": 500},
            treatment={"resolved": True, "regression_introduced": False, "wall_clock_s": 110, "tokens_in": 1100, "tokens_out": 520},
        )

    report = aggregate(run_dir)
    assert "metrics" in report
    for k in METRIC_KEYS:
        assert k in report["metrics"]
        m = report["metrics"][k]
        for field in ("n", "mean_delta", "ci_lo", "ci_hi", "p_wilcoxon", "p_holm"):
            assert field in m
    assert report["header"]["n_pairs"] == 3
    assert report["decision"]["verdict"] in {"ship", "kill", "inconclusive"}


def test_aggregate_decision_flips_when_treatment_worse(tmp_path):
    run_dir = tmp_path / "run-bad"
    run_dir.mkdir()
    for i in range(6):
        _write_pair(
            run_dir,
            f"t{i}",
            vanilla={"resolved": True, "regression_introduced": False, "wall_clock_s": 100, "tokens_in": 1000, "tokens_out": 500},
            treatment={"resolved": False, "regression_introduced": True, "wall_clock_s": 200, "tokens_in": 1500, "tokens_out": 700},
        )
    report = aggregate(run_dir)
    # Resolved went down and regression went up; verdict cannot be ship.
    assert report["decision"]["verdict"] in {"kill", "inconclusive"}


def test_aggregate_handles_missing_pairs(tmp_path):
    run_dir = tmp_path / "run-incomplete"
    run_dir.mkdir()
    # Only vanilla side -- aggregator must not crash.
    (run_dir / "lonely.vanilla.json").write_text(
        json.dumps({"task_id": "lonely", "arm": "vanilla", "resolved": True, "regression_introduced": False})
    )
    report = aggregate(run_dir)
    assert report["header"]["n_pairs"] == 0


def test_render_markdown_does_not_crash_on_n1(tmp_path):
    run_dir = tmp_path / "run-n1"
    run_dir.mkdir()
    _write_pair(
        run_dir,
        "only",
        vanilla={"resolved": False, "regression_introduced": True, "wall_clock_s": 50, "tokens_in": 100, "tokens_out": 50},
        treatment={"resolved": True, "regression_introduced": False, "wall_clock_s": 60, "tokens_in": 120, "tokens_out": 55},
    )
    report = aggregate(run_dir)
    md = _render_markdown(report)
    assert "Eval Report" in md
    assert "verdict" in md
