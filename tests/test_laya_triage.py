"""Checks for the small exploratory Laya intake evaluation."""
from __future__ import annotations

import json

import pytest

from eval.laya_triage.run import CASES, load_cases, summarize


def test_frozen_cases_have_expected_balance():
    cases = load_cases(CASES)
    assert len(cases) == 12
    assert {kind: sum(case["expected_kind"] == kind for case in cases)
            for kind in ("localized", "multifile", "investigation")} == {
                "localized": 4, "multifile": 4, "investigation": 4,
            }


def test_duplicate_cases_fail_before_any_run(tmp_path):
    rows = load_cases(CASES)
    fixture = tmp_path / "duplicate.json"
    fixture.write_text(json.dumps({"schema_version": 1, "cases": [rows[0], rows[0]]}))
    with pytest.raises(ValueError, match="unique"):
        load_cases(fixture)


def test_summary_counts_abstention_as_not_correct():
    rows = [
        {"source": "laya", "kind": "localized", "expected_kind": "localized",
         "elapsed_ms": 10},
        {"source": "laya", "kind": "uncertain", "expected_kind": "multifile",
         "elapsed_ms": 20},
    ]
    result = summarize(rows, "laya")
    assert result["n"] == 2
    assert result["correct"] == 1
    assert result["abstentions"] == 1
