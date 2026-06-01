"""U8: gate_prm — PRM measurement-only gate.

Audit 2026-06-01 §B1: emit a signal_source="prm" audit event per Edit when
RC_PRM_GATE=1. Never blocks. Defaults OFF until a calibrated PRM lands.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "hooks"))

import _dispatch as dsp  # noqa: E402


def test_prm_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RC_PRM_GATE", raising=False)
    out = dsp.gate_prm(
        file_path="/tmp/x.py",
        before_src="a",
        after_src="b",
        plan_text="some plan",
    )
    assert out.action == "pass"
    assert out.signal_source == ""


def test_prm_gate_no_plan_returns_audit_only_skip(monkeypatch):
    monkeypatch.setenv("RC_PRM_GATE", "1")
    out = dsp.gate_prm(
        file_path="/tmp/x.py",
        before_src="a",
        after_src="b",
        plan_text=None,
    )
    assert out.decision == "audit_only"
    assert out.signal_source == "prm"
    assert out.reason == "prm_skip:no_plan_md"


def test_prm_gate_unavailable_when_gen_client_raises(monkeypatch):
    monkeypatch.setenv("RC_PRM_GATE", "1")
    # Monkeypatch gen_client.score_plan_grounding to raise.
    from src import gen_client  # type: ignore
    monkeypatch.setattr(
        gen_client, "score_plan_grounding",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("backend down")),
    )
    out = dsp.gate_prm(
        file_path="/tmp/x.py",
        before_src="a",
        after_src="b",
        plan_text="plan with claim",
    )
    assert out.decision == "audit_only"
    assert out.signal_source == "prm"
    assert out.reason == "prm_unavailable"


def test_prm_gate_score_from_verdict(monkeypatch):
    monkeypatch.setenv("RC_PRM_GATE", "1")
    from src import gen_client  # type: ignore
    monkeypatch.setattr(
        gen_client, "score_plan_grounding",
        lambda plan, diff: {"Q1": 1, "Q2": 0, "Q3": 1},
    )
    out = dsp.gate_prm(
        file_path="/tmp/x.py",
        before_src="line1\nline2\n",
        after_src="line1\nline2\nline3\n",
        plan_text="plan with claim about x",
    )
    assert out.decision == "allowed"
    assert out.signal_source == "prm"
    assert "prm_score=0.67" in out.reason
    assert out.audit_extra.get("prm_score") == pytest.approx(2 / 3)
    assert out.audit_extra.get("prm_yes") == 2
    assert out.audit_extra.get("prm_total") == 3
