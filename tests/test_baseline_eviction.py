"""Regression tests for the audit-hostile/2026-09-19-fixes _BASELINES eviction.

Covers:
- LRU eviction at S2_BASELINE_MAX_SESSIONS
- TTL eviction at S2_BASELINE_TTL_S
- Test helper _baseline_count()
"""
from __future__ import annotations

import time

import pytest

from src import s2_core


@pytest.fixture(autouse=True)
def _clear_baselines():
    s2_core._clear_session_baselines()
    yield
    s2_core._clear_session_baselines()


def test_baseline_lru_cap(monkeypatch):
    monkeypatch.setattr(s2_core, "_BASELINE_MAX_SESSIONS", 3)
    monkeypatch.setattr(s2_core, "_BASELINE_TTL_S", 0.0)  # disable TTL for this test
    # Insert 3 sessions, each with a unique baseline dict.
    for i in range(3):
        s2_core._set_session_baseline(f"session-{i}", {f"path-{i}": f"vec-{i}"})
    assert s2_core._baseline_count() == 3
    # Adding a 4th evicts session-0.
    s2_core._set_session_baseline("session-3", {"path-3": "vec-3"})
    assert s2_core._baseline_count() == 3
    assert s2_core._get_session_baseline("session-0") is None
    assert s2_core._get_session_baseline("session-3") is not None


def test_baseline_lru_touch_reorders():
    s2_core._set_session_baseline("a", {"path": "v1"})
    s2_core._set_session_baseline("b", {"path": "v2"})
    s2_core._set_session_baseline("c", {"path": "v3"})
    # Touch 'a' — 'a' is now most-recent.
    s2_core._get_session_baseline("a")
    s2_core._BASELINE_MAX_SESSIONS = 3
    # Add 'd' — 'b' should be evicted (oldest non-touched).
    s2_core._BASELINE_MAX_SESSIONS = 3
    s2_core._set_session_baseline("d", {"path": "v4"})
    assert s2_core._get_session_baseline("a") is not None
    assert s2_core._get_session_baseline("b") is None
    assert s2_core._get_session_baseline("c") is not None
    assert s2_core._get_session_baseline("d") is not None


def test_baseline_ttl_eviction(monkeypatch):
    monkeypatch.setattr(s2_core, "_BASELINE_TTL_S", 0.5)
    s2_core._set_session_baseline("exp", {"path": "v"})
    assert s2_core._get_session_baseline("exp") is not None
    time.sleep(0.7)
    # TTL sweep happens on read.
    assert s2_core._get_session_baseline("exp") is None
