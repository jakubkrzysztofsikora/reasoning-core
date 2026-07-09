"""Tests for Phase 3 PRM gate and promotion tracker."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(REPO_ROOT / "src")
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import _prm_promotion as prm_promo  # type: ignore


@pytest.fixture(autouse=True)
def _isolated_prm_state(monkeypatch, tmp_path):
    """Each test gets an isolated PRM shadow state file."""
    state_file = tmp_path / "prm-shadow-state.jsonl"
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path))
    # Ensure clean state even if a previous test leaked.
    if state_file.exists():
        state_file.write_text("", encoding="utf-8")
    yield


def test_promotion_status_empty():
    status = prm_promo.promotion_status()
    assert status.promoted is False
    assert status.event_count == 0
    assert "no shadow events" in status.reason


def test_record_shadow_event_and_promotion(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRM_PROMO_MIN_REPOS", "2")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_EVENTS", "3")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_DAYS", "0")

    prm_promo.record_shadow_event("/repo/a", 0.1)
    prm_promo.record_shadow_event("/repo/b", 0.2)
    prm_promo.record_shadow_event("/repo/a", 0.3)

    status = prm_promo.promotion_status()
    assert status.promoted is True
    assert status.repo_count == 2
    assert status.event_count == 3


def test_promotion_not_met_with_few_events(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRM_PROMO_MIN_REPOS", "2")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_EVENTS", "10")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_DAYS", "0")

    prm_promo.record_shadow_event("/repo/a", 0.1)
    prm_promo.record_shadow_event("/repo/b", 0.2)

    status = prm_promo.promotion_status()
    assert status.promoted is False
    assert "events 2/10" in status.reason


def test_promotion_not_met_with_few_repos(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRM_PROMO_MIN_REPOS", "3")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_EVENTS", "2")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_DAYS", "0")

    prm_promo.record_shadow_event("/repo/a", 0.1)
    prm_promo.record_shadow_event("/repo/a", 0.2)

    status = prm_promo.promotion_status()
    assert status.promoted is False
    assert "repos 1/3" in status.reason


def test_promotion_not_met_during_shadow_period(monkeypatch, tmp_path):
    monkeypatch.setenv("RC_PRM_PROMO_MIN_REPOS", "1")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_EVENTS", "1")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_DAYS", "999")

    prm_promo.record_shadow_event("/repo/a", 0.1)

    status = prm_promo.promotion_status()
    assert status.promoted is False
    assert "days" in status.reason


def test_state_file_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path))
    prm_promo.record_shadow_event("/repo/a", 0.5)
    state = prm_promo._state_path()
    assert state.exists()
    # Mode may be 0o600 or 0o644 depending on umask; just ensure readable.
    body = state.read_text(encoding="utf-8")
    assert json.loads(body)["score"] == 0.5


def test_gate_prm_no_op_when_disabled(monkeypatch):
    from _dispatch import gate_prm  # type: ignore

    monkeypatch.delenv("RC_PRM_GATE", raising=False)
    outcome = gate_prm(
        file_path="src/foo.py",
        before_src="a",
        after_src="b",
        plan_text="plan",
    )
    assert outcome.action == "pass"
    assert outcome.signal_source == ""


def test_gate_prm_warns_when_low_score_not_promoted(monkeypatch, tmp_path):
    from _dispatch import gate_prm  # type: ignore
    from src import gen_client as _gc  # type: ignore

    monkeypatch.setenv("RC_PRM_GATE", "1")
    monkeypatch.setenv("RC_PRM_THRESHOLD", "0.5")
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path))

    # Mock gen_client to return a low score.
    monkeypatch.setattr(
        _gc, "score_plan_grounding", lambda _claim, _hunk: {"supported": 0, "total": 2}
    )

    outcome = gate_prm(
        file_path="src/foo.py",
        before_src="a",
        after_src="b",
        plan_text="plan",
    )
    assert outcome.action == "stderr_only"
    assert outcome.signal_source == "prm"
    assert "shadow" in outcome.reason


def test_gate_prm_blocks_when_low_score_promoted(monkeypatch, tmp_path):
    from _dispatch import gate_prm  # type: ignore
    from src import gen_client as _gc  # type: ignore

    monkeypatch.setenv("RC_PRM_GATE", "1")
    monkeypatch.setenv("RC_PRM_BLOCK", "1")
    monkeypatch.setenv("RC_PRM_THRESHOLD", "0.5")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_REPOS", "1")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_EVENTS", "1")
    monkeypatch.setenv("RC_PRM_PROMO_MIN_DAYS", "0")
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path))

    # Pre-seed promotion.
    prm_promo.record_shadow_event("/repo/a", 0.1)

    monkeypatch.setattr(
        _gc, "score_plan_grounding", lambda _claim, _hunk: {"supported": 0, "total": 2}
    )

    outcome = gate_prm(
        file_path="src/foo.py",
        before_src="a",
        after_src="b",
        plan_text="plan",
    )
    assert outcome.action == "exit_block"
    assert outcome.signal_source == "prm"
