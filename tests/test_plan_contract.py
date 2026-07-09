"""Tests for the Phase 1 plan-to-contract compiler."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import _plan_contract as pc  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contract_dict() -> dict:
    return {
        "version": "1.0",
        "allowed_paths": ["src/**/*.py", "tests/**/*.py"],
        "forbidden_paths": [".claude/settings.json"],
        "required_tests": ["tests/test_foo.py"],
        "import_rules": [
            {
                "id": "no_auth_in_payments",
                "severity": "deny",
                "scope": "src/payments/**",
                "forbidden_imports": ["src.auth", "auth"],
                "message": "Payments must not import auth",
            }
        ],
        "invariants": [
            {
                "id": "no_subprocess_shell",
                "severity": "warn",
                "description": "Avoid shell=True",
                "pattern": r"subprocess\.\w+\(.*shell\s*=\s*True",
            }
        ],
        "phases": [
            {
                "name": "Phase 1",
                "active": True,
                "allowed_paths": ["src/foo.py"],
                "required_tests": ["tests/test_foo.py"],
            },
            {
                "name": "Phase 2",
                "active": False,
                "allowed_paths": ["src/bar.py"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Schema / loading
# ---------------------------------------------------------------------------


def test_contract_from_dict_parses_all_sections():
    c = pc.Contract.from_dict(_contract_dict())
    assert c.version == "1.0"
    assert "src/**/*.py" in c.allowed_paths
    assert ".claude/settings.json" in c.forbidden_paths
    assert c.required_tests == ["tests/test_foo.py"]
    assert len(c.import_rules) == 1
    assert c.import_rules[0].id == "no_auth_in_payments"
    assert c.import_rules[0].forbidden_imports == ["src.auth", "auth"]
    assert len(c.invariants) == 1
    assert c.invariants[0].id == "no_subprocess_shell"
    assert len(c.phases) == 2
    assert c.phases[0].active is True


def test_contract_load_prefers_explicit_yaml(tmp_path):
    contract_yaml = tmp_path / ".reasoning-core" / "contract.yaml"
    contract_yaml.parent.mkdir()
    contract_yaml.write_text(
        """
version: '1.0'
allowed_paths:
  - src/foo.py
forbidden_paths:
  - src/secret.py
""",
        encoding="utf-8",
    )
    plan = tmp_path / "PLAN.md"
    plan.write_text("- `src/bar.py` — ~100 LOC\n", encoding="utf-8")

    c = pc.Contract.load(project_root=str(tmp_path), plan_path=plan)
    assert c.source == str(contract_yaml)
    assert "src/foo.py" in c.allowed_paths
    assert "src/secret.py" in c.forbidden_paths
    # Explicit contract wins over PLAN.md derivation.
    assert "src/bar.py" not in c.allowed_paths


def test_contract_load_falls_back_to_plan(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        "- `src/foo.py` — ~100 LOC\n- `src/bar.py` — ~50 LOC\n",
        encoding="utf-8",
    )
    c = pc.Contract.load(project_root=str(tmp_path), plan_path=plan)
    assert c.source == str(plan)
    assert "src/foo.py" in c.allowed_paths
    assert "src/bar.py" in c.allowed_paths


def test_contract_load_returns_empty_when_no_sources(tmp_path):
    c = pc.Contract.load(project_root=str(tmp_path))
    assert c.source == "empty"
    assert c.check_path("anything.py") is None


# ---------------------------------------------------------------------------
# Path checks
# ---------------------------------------------------------------------------


def test_allowed_path_permits_matching_file():
    c = pc.Contract.from_dict({"allowed_paths": ["src/**/*.py"]})
    assert c.check_path("src/foo.py") is None


def test_allowed_path_blocks_non_matching_file():
    c = pc.Contract.from_dict({"allowed_paths": ["src/**/*.py"]})
    v = c.check_path("docs/readme.md")
    assert v is not None
    assert v.kind == "path"
    assert v.severity == "deny"


def test_forbidden_path_wins_over_allowed():
    c = pc.Contract.from_dict(
        {"allowed_paths": ["src/**/*.py"], "forbidden_paths": ["src/secret.py"]}
    )
    assert c.check_path("src/secret.py") is not None
    assert c.check_path("src/foo.py") is None


def test_active_phase_limits_scope():
    c = pc.Contract.from_dict(
        {
            "allowed_paths": ["src/**/*.py"],
            "phases": [
                {
                    "name": "P1",
                    "active": True,
                    "allowed_paths": ["src/foo.py"],
                }
            ],
        }
    )
    assert c.check_path("src/foo.py") is None
    v = c.check_path("src/bar.py")
    assert v is not None
    assert v.kind == "phase"


def test_active_phase_forbidden_path():
    c = pc.Contract.from_dict(
        {
            "phases": [
                {
                    "name": "P1",
                    "active": True,
                    "forbidden_paths": ["src/bar.py"],
                }
            ],
        }
    )
    assert c.check_path("src/foo.py") is None
    assert c.check_path("src/bar.py") is not None


def test_absolute_path_matches_repo_relative_glob():
    c = pc.Contract.from_dict({"allowed_paths": ["src/**/*.py"]})
    assert c.check_path("/home/user/repo/src/foo.py") is None


def test_empty_contract_allows_everything():
    c = pc.Contract()
    assert c.check_path("anything.txt") is None


# ---------------------------------------------------------------------------
# Import checks
# ---------------------------------------------------------------------------


def test_python_forbidden_import_detected():
    c = pc.Contract.from_dict(
        {
            "import_rules": [
                {
                    "id": "no_auth",
                    "scope": "src/payments/**",
                    "forbidden_imports": ["src.auth"],
                }
            ]
        }
    )
    hits = c.check_imports(
        "src/payments/processor.py",
        "from src.auth import verify_token\n",
    )
    assert len(hits) == 1
    assert hits[0].rule_id == "no_auth"
    assert hits[0].kind == "import"


def test_python_import_scope_mismatch_is_allowed():
    c = pc.Contract.from_dict(
        {
            "import_rules": [
                {
                    "id": "no_auth",
                    "scope": "src/payments/**",
                    "forbidden_imports": ["src.auth"],
                }
            ]
        }
    )
    hits = c.check_imports(
        "src/orders/processor.py",
        "from src.auth import verify_token\n",
    )
    assert hits == []


def test_js_forbidden_import_detected():
    c = pc.Contract.from_dict(
        {
            "import_rules": [
                {
                    "id": "no_auth",
                    "scope": "src/payments/**",
                    "forbidden_imports": ["../auth"],
                }
            ]
        }
    )
    hits = c.check_imports(
        "src/payments/widget.ts",
        "import { verify } from '../auth';\n",
    )
    assert len(hits) == 1
    assert hits[0].matched_text == "../auth"


def test_forbidden_import_severity_warn():
    c = pc.Contract.from_dict(
        {
            "import_rules": [
                {
                    "id": "discouraged",
                    "severity": "warn",
                    "scope": "**",
                    "forbidden_imports": ["deprecated"],
                }
            ]
        }
    )
    hits = c.check_imports("src/foo.py", "import deprecated\n")
    assert hits[0].severity == "warn"


# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------


def test_invariant_pattern_detected():
    c = pc.Contract.from_dict(
        {
            "invariants": [
                {
                    "id": "no_shell",
                    "severity": "warn",
                    "description": "Avoid shell=True",
                    "pattern": r"shell\s*=\s*True",
                }
            ]
        }
    )
    hits = c.check_invariants(
        "src/runner.py",
        "subprocess.run(cmd, shell=True)\n",
    )
    assert len(hits) == 1
    assert hits[0].rule_id == "no_shell"
    assert hits[0].line == 1


def test_invariant_empty_pattern_is_no_op():
    c = pc.Contract.from_dict(
        {"invariants": [{"id": "no_shell", "severity": "warn"}]}
    )
    assert c.check_invariants("src/runner.py", "pass\n") == []


# ---------------------------------------------------------------------------
# PLAN.md derivation
# ---------------------------------------------------------------------------


def test_plan_derives_allowed_paths():
    plan = """
# PLAN

## Stage 1 — Plan
Spec file: `src/foo.py`

## Stage 2 — Implementation
- `src/bar.py` — ~100 LOC
"""
    c = pc.Contract.from_plan(plan)
    assert "src/foo.py" in c.allowed_paths
    assert "src/bar.py" in c.allowed_paths


def test_plan_extracts_forbidden_paths():
    plan = """
# PLAN

## Files I will explicitly NOT touch
- `src/secret.py`
- `src/auth/core.py`

## Stage 1
- `src/foo.py` — ~100 LOC
"""
    c = pc.Contract.from_plan(plan)
    assert "src/secret.py" in c.forbidden_paths
    assert "src/auth/core.py" in c.forbidden_paths
    assert "src/foo.py" in c.allowed_paths


def test_plan_extracts_phases():
    plan = """
# PLAN

## Stage 1 — Plan
- `src/foo.py` — ~100 LOC

## Stage 2 — Implementation
- `src/bar.py` — ~100 LOC
"""
    c = pc.Contract.from_plan(plan)
    assert len(c.phases) == 2
    assert c.phases[0].active is True
    assert c.phases[1].active is False


# ---------------------------------------------------------------------------
# Integration with _dispatch.gate_plan_grounding
# ---------------------------------------------------------------------------


def test_gate_plan_grounding_uses_explicit_contract_yaml(
    tmp_path, monkeypatch
):
    """End-to-end: explicit contract.yaml blocks forbidden import."""
    from _dispatch import gate_plan_grounding  # type: ignore

    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_MODE", "copilot")

    contract_yaml = tmp_path / ".reasoning-core" / "contract.yaml"
    contract_yaml.parent.mkdir()
    contract_yaml.write_text(
        """
version: '1.0'
import_rules:
  - id: no_requests
    severity: deny
    scope: '**'
    forbidden_imports:
      - requests
""",
        encoding="utf-8",
    )
    plan = tmp_path / "PLAN.md"
    plan.write_text("- `src/foo.py` — ~100 LOC\n", encoding="utf-8")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))

    outcome = gate_plan_grounding(
        file_path="src/foo.py",
        after_src="import requests\n",
        path_check=False,
    )
    assert outcome.action == "exit_block"
    assert "contract_violation" in outcome.reason
    assert "no_requests" in outcome.reason


def test_gate_plan_grounding_backward_compatible_plan_drift(
    tmp_path, monkeypatch
):
    """Plan-derived contract still emits legacy plan_impl_drift reason."""
    from _dispatch import gate_plan_grounding  # type: ignore

    monkeypatch.setenv("RC_PLAN_GROUNDING", "2")
    monkeypatch.setenv("RC_MODE", "copilot")

    plan = tmp_path / "PLAN.md"
    plan.write_text("- `src/foo.py` — ~100 LOC\n", encoding="utf-8")
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))

    outcome = gate_plan_grounding(file_path="src/bar.py")
    assert outcome.action == "exit_block"
    assert outcome.reason == "plan_impl_drift"
    assert "not in PLAN.md" in outcome.stderr


# ---------------------------------------------------------------------------
# Violation helpers
# ---------------------------------------------------------------------------


def test_first_deny_prefers_deny():
    c = pc.Contract()
    v1 = pc.Violation(kind="import", rule_id="warn_rule", severity="warn", message="x")
    v2 = pc.Violation(kind="import", rule_id="deny_rule", severity="deny", message="y")
    assert c.first_deny([v1, v2]).rule_id == "deny_rule"


def test_first_deny_returns_none_when_only_warns():
    c = pc.Contract()
    v1 = pc.Violation(kind="import", rule_id="warn_rule", severity="warn", message="x")
    assert c.first_deny([v1]) is None
