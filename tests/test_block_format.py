"""Unit tests for block message formatter (_block_format.py)."""
from __future__ import annotations

import os
import sys

HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "hooks")
sys.path.insert(0, HOOKS_DIR)

import _block_format  # noqa: E402


def test_hints_cover_all_risk_labels():
    """Every risk label used in top3 matching has a corresponding HINT."""
    # The 11 risk labels from s2_core.py
    risk_labels = [
        "cyclomatic", "fan_in", "fan_out", "depth", "churn",
        "coupling", "cohesion", "novelty",
        "session_centroid_drift", "project_fan_in", "project_coupling",
    ]
    for label in risk_labels:
        assert label in _block_format.HINTS, f"Missing hint for risk label: {label}"


def test_format_block_includes_fired_conditions():
    """Block message includes fired_conditions with margins."""
    report = {
        "architectural_impact_score": 0.35,
        "coherence_delta": 0.15,
        "risk_vector": [0.5, 0.3, 0.1, 0.2, 0.8, 0.4, 0.1, 0.9, 0.0, 0.0, 0.0],
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth", "churn",
            "coupling", "cohesion", "novelty",
            "session_centroid_drift", "project_fan_in", "project_coupling",
        ],
        "regression_detected": True,
        "human_summary": "High churn and novelty detected",
        "file_kind": "source_code",
        "cd_threshold": 0.09,
        "fired_conditions": ["ais_below_threshold", "dim_ceiling_breached"],
        "fired_dims": ["churn", "novelty"],
        "fired_margins": {"ais_below_threshold": 0.05, "dim_churn": 0.10, "dim_novelty": 0.15},
    }
    result = _block_format.format_block("src/foo.py", report, is_retry=False)
    assert "fired conditions" in result
    assert "ais_below_threshold" in result
    assert "dim_ceiling_breached" in result
    assert "margin=0.05" in result


def test_format_block_includes_actionable_hints():
    """Block message includes actionable per-dim hints, not just generic text."""
    report = {
        "architectural_impact_score": 0.38,
        "coherence_delta": 0.08,
        "risk_vector": [0.6, 0.2, 0.1, 0.1, 0.95, 0.3, 0.1, 0.4, 0.0, 0.0, 0.0],
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth", "churn",
            "coupling", "cohesion", "novelty",
            "session_centroid_drift", "project_fan_in", "project_coupling",
        ],
        "regression_detected": True,
        "human_summary": "High cyclomatic and churn",
        "file_kind": "source_code",
        "cd_threshold": 0.09,
        "fired_conditions": ["dim_ceiling_breached"],
        "fired_dims": ["churn"],
        "fired_margins": {"dim_churn": 0.30},
    }
    result = _block_format.format_block("src/foo.py", report, is_retry=False)
    # Verify hints are more than just 2-3 words
    assert "Split this file" in result  # churn hint
    assert "Extract helper functions" in result  # cyclomatic hint (top3)


def test_format_block_includes_retry_warning():
    """Retry detected adds retry-specific message."""
    report = {
        "architectural_impact_score": 0.38,
        "coherence_delta": 0.08,
        "risk_vector": [0.6, 0.2, 0.1, 0.1, 0.1, 0.3, 0.1, 0.4, 0.0, 0.0, 0.0],
        "risk_labels": [
            "cyclomatic", "fan_in", "fan_out", "depth", "churn",
            "coupling", "cohesion", "novelty",
            "session_centroid_drift", "project_fan_in", "project_coupling",
        ],
        "regression_detected": True,
        "human_summary": "Test",
        "file_kind": "source_code",
        "cd_threshold": 0.09,
        "fired_conditions": [],
        "fired_margins": {},
    }
    result = _block_format.format_block("src/foo.py", report, is_retry=True)
    assert "RETRY DETECTED" in result
    assert "RECOVERY PATH" in result


def test_top3_handles_missing_labels():
    """top3 returns empty list when risk_labels don't match risk_vector."""
    assert _block_format.top3({"risk_vector": [1, 2, 3]}) == []
    assert _block_format.top3({"risk_vector": [1, 2], "risk_labels": ["a"]}) == []


def test_fired_lines_empty_when_no_conditions():
    """_fired_lines returns empty string when no fired_conditions."""
    assert _block_format._fired_lines({}) == ""
    assert _block_format._fired_lines({"fired_conditions": []}) == ""
