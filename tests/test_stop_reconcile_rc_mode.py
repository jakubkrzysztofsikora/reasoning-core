"""Tests for the stop hook RC_MODE resolution from .envrc.local."""
from __future__ import annotations

import importlib
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


def _fresh():
    """Re-import stop_reconcile cleanly."""
    if "stop_reconcile" in sys.modules:
        importlib.reload(sys.modules["stop_reconcile"])
    else:
        importlib.import_module("stop_reconcile")
    return sys.modules["stop_reconcile"]


def test_resolve_rc_mode_from_env(monkeypatch):
    monkeypatch.setenv("RC_MODE", "copilot")
    sr = _fresh()
    assert sr._resolve_rc_mode("/tmp/whatever") == "copilot"


def test_resolve_rc_mode_from_envrc_local(monkeypatch, tmp_path):
    monkeypatch.delenv("RC_MODE", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".envrc.local").write_text(
        "# other exports\nexport FOO=bar\n"
        "# >>> rc enforcement >>>\n"
        "export RC_MODE=copilot\n"
        "export RC_SHADOW_MODE=0\n"
        "# <<< rc enforcement <<<\n",
        encoding="utf-8",
    )
    sr = _fresh()
    assert sr._resolve_rc_mode(str(project)) == "copilot"


def test_resolve_rc_mode_defaults_to_advise(monkeypatch, tmp_path):
    monkeypatch.delenv("RC_MODE", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    sr = _fresh()
    assert sr._resolve_rc_mode(str(project)) == "advise"


def test_resolve_rc_mode_when_envrc_has_no_enforcement_block(monkeypatch, tmp_path):
    monkeypatch.delenv("RC_MODE", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".envrc.local").write_text("export FOO=bar\n", encoding="utf-8")
    sr = _fresh()
    assert sr._resolve_rc_mode(str(project)) == "advise"