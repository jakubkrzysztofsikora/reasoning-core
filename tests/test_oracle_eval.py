"""Tests for eval/oracle_eval.py — the labeled oracle eval harness."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT / "src" / "hooks") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src" / "hooks"))

import eval.oracle_eval as oev  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


def test_generate_edits_default_count_and_categories():
    cases = oev.generate_edits()
    assert len(cases) == 100
    categories = {c.category for c in cases}
    assert categories == {
        "in_plan",
        "scope_creep",
        "import_violation",
        "syntax_error",
        "test_omission",
    }
    for cat in categories:
        assert sum(1 for c in cases if c.category == cat) == 20


def test_generate_edits_per_category_is_configurable():
    cases = oev.generate_edits(per_category=5)
    assert len(cases) == 25
    assert sum(1 for c in cases if c.category == "in_plan") == 5


def test_edit_case_labels_are_boolean():
    cases = oev.generate_edits(per_category=1)
    assert any(c.expected_block for c in cases)
    assert any(not c.expected_block for c in cases)


# ---------------------------------------------------------------------------
# Per-category behaviour
# ---------------------------------------------------------------------------


def test_in_plan_cases_are_allowed():
    results = oev.run_eval(per_category=5)
    in_plan = [r for r in results if r.case.category == "in_plan"]
    assert in_plan
    assert all(not r.blocked for r in in_plan)
    assert all(r.oracle_clean for r in in_plan)


def test_scope_creep_cases_are_blocked():
    results = oev.run_eval(per_category=5)
    scope_creep = [r for r in results if r.case.category == "scope_creep"]
    assert scope_creep
    assert all(r.blocked for r in scope_creep)
    assert all(any("path:" in reason for reason in r.reasons) for r in scope_creep)


def test_import_violation_cases_are_blocked():
    results = oev.run_eval(per_category=5)
    import_viol = [r for r in results if r.case.category == "import_violation"]
    assert import_viol
    assert all(r.blocked for r in import_viol)
    assert all(any("import:" in reason for reason in r.reasons) for r in import_viol)


def test_syntax_error_cases_are_blocked():
    results = oev.run_eval(per_category=5)
    syntax = [r for r in results if r.case.category == "syntax_error"]
    assert syntax
    assert all(r.blocked for r in syntax)
    assert all(any("oracle:" in reason for reason in r.reasons) for r in syntax)


def test_test_omission_currently_false_negative():
    """test_omission is expected to block, but the contract does not enforce
    required_tests yet.  The harness documents this as a false negative."""
    results = oev.run_eval(per_category=5)
    omissions = [r for r in results if r.case.category == "test_omission"]
    assert omissions
    assert all(not r.blocked for r in omissions)


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------


def test_compute_metrics_on_perfect_subset():
    """Hand-craft a tiny result set and verify metric formulas."""
    contract = oev.base_contract_dict()
    plan = oev.base_plan_text()

    def make(id_, cat, expected, blocked):
        return oev.CaseResult(
            case=oev.EditCase(
                id=id_,
                category=cat,
                expected_block=expected,
                file_path="src/core/engine.py",
                before_src="pass\n",
                after_src="x = 1\n",
                contract_dict=contract,
                plan_text=plan,
            ),
            blocked=blocked,
        )

    results = [
        make("tp1", "scope_creep", True, True),
        make("tp2", "import_violation", True, True),
        make("fn1", "test_omission", True, False),
        make("tn1", "in_plan", False, False),
        make("tn2", "in_plan", False, False),
        make("fp1", "in_plan", False, True),
    ]
    metrics = oev.compute_metrics(results)
    assert metrics.tp == 2
    assert metrics.fp == 1
    assert metrics.tn == 2
    assert metrics.fn == 1
    assert abs(metrics.precision - 2 / 3) < 1e-9
    assert abs(metrics.recall - 2 / 3) < 1e-9
    assert abs(metrics.fpr - 1 / 3) < 1e-9
    assert metrics.per_category["scope_creep"]["tp"] == 1


def test_compute_metrics_empty_results():
    metrics = oev.compute_metrics([])
    assert metrics.n == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
    assert metrics.fpr == 0.0


# ---------------------------------------------------------------------------
# Reporting / CLI
# ---------------------------------------------------------------------------


def test_print_report_does_not_raise(capsys):
    results = oev.run_eval(per_category=2)
    oev.print_report(results)
    captured = capsys.readouterr()
    assert "oracle eval report" in captured.out
    assert "precision=" in captured.out


def test_results_to_dict_roundtrip():
    results = oev.run_eval(per_category=2)
    payload = oev.results_to_dict(results)
    assert payload["metadata"]["corpus"] == "synthetic"
    assert payload["metadata"]["real_pr_subset"] is False
    assert payload["metrics"]["n"] == len(results)
    assert len(payload["cases"]) == len(results)
    # Every case serialises to JSON.
    assert json.dumps(payload)


def test_main_cli_writes_json(tmp_path):
    out = tmp_path / "report.json"
    rc = oev.main(["--per-category", "2", "--out", str(out), "--quiet"])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metadata"]["n_cases"] == 10
    assert "precision" in data["metrics"]


def test_main_cli_quiet_suppresses_report(capsys, tmp_path):
    out = tmp_path / "report.json"
    oev.main(["--per-category", "1", "--out", str(out), "--quiet"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def test_base_contract_dict_has_expected_rules():
    contract = oev.pc.Contract.from_dict(oev.base_contract_dict())
    assert contract.allowed_paths
    assert contract.forbidden_paths
    assert contract.import_rules
    assert contract.invariants
    assert contract.required_tests


def test_base_plan_text_derives_allowed_paths():
    contract = oev.pc.Contract.from_plan(oev.base_plan_text())
    assert "src/core/engine.py" in contract.allowed_paths
    assert "src/api/handlers.py" in contract.allowed_paths
    assert "src/legacy.py" in contract.forbidden_paths
