"""Regression: supervisor health timeout is now 15.0s default (was 3.0s).

Audit-hostile/2026-09-19-fixes: the previous _HEALTH_TIMEOUT_S = 3.0s was
below worst-case Mamba CPU inference latency, so the supervisor tripped a
false SIGTERM and a 60s circuit-break on every busy session.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


# The supervisor lives in src/ and uses sibling-relative imports
# (`from _supervisor_env import ...`). Add src/ to sys.path so the
# sibling imports resolve, then load it.
SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Drop any cached module so we get a fresh reload each time this test
# module runs.
for name in list(sys.modules):
    if name in (
        "sidecar_supervisor",
        "_supervisor_env",
        "_supervisor_broker",
        "_supervisor_recalibrate",
    ):
        del sys.modules[name]

import sidecar_supervisor as supervisor  # noqa: E402


def test_default_health_timeout_at_least_10s():
    importlib.reload(supervisor)
    assert supervisor._HEALTH_TIMEOUT_S >= 10.0, (
        f"supervisor health timeout {supervisor._HEALTH_TIMEOUT_S}s is below the "
        "worst-case inference latency. CPU Mamba-130M forward passes run "
        "2-4.5s and tree-sitter adds another 0.5-1s."
    )


def test_default_health_timeout_env_override(monkeypatch):
    monkeypatch.setenv("S2_HEALTH_TIMEOUT_S", "20.0")
    importlib.reload(supervisor)
    assert supervisor._HEALTH_TIMEOUT_S == 20.0


def test_default_grace_at_least_30s():
    importlib.reload(supervisor)
    assert supervisor._HEALTH_GRACE_S >= 30.0, (
        f"startup grace {supervisor._HEALTH_GRACE_S}s is too short for "
        "Mamba cold start (model + tokenizer load)."
    )


def test_default_grace_env_override(monkeypatch):
    monkeypatch.setenv("S2_HEALTH_GRACE_S", "90.0")
    importlib.reload(supervisor)
    assert supervisor._HEALTH_GRACE_S == 90.0
