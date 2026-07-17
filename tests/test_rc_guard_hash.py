"""Tests for rc guard-hash integrity checks."""
from __future__ import annotations

import hashlib
import importlib
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


@pytest.fixture
def fresh_rc_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))
    import rc_cli
    importlib.reload(rc_cli)
    return rc_cli


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_guard_hash_detects_tampering(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    fresh_rc_cli._init_guard_hashes([str(guard)])

    guard.write_text("# tampered", encoding="utf-8")

    result = fresh_rc_cli._verify_guard_hash(str(guard))
    assert result is False


def test_guard_hash_passes_intact(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    fresh_rc_cli._init_guard_hashes([str(guard)])

    result = fresh_rc_cli._verify_guard_hash(str(guard))
    assert result is True


def test_guard_hash_returns_false_when_store_missing(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    # No store file → must return False (TAMPERED)
    result = fresh_rc_cli._verify_guard_hash(str(guard))
    assert result is False


def test_guard_hash_returns_false_when_file_missing_from_store(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    # Store exists but does not contain this file
    store = state_dir / "guard_hashes.json"
    store.write_text("{}", encoding="utf-8")

    result = fresh_rc_cli._verify_guard_hash(str(guard))
    assert result is False


def test_guard_hash_returns_false_when_store_corrupt(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    store = state_dir / "guard_hashes.json"
    store.write_text("not json", encoding="utf-8")

    result = fresh_rc_cli._verify_guard_hash(str(guard))
    assert result is False


def test_guard_hash_returns_false_when_file_missing(fresh_rc_cli, tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    store = state_dir / "guard_hashes.json"
    store.write_text("{}", encoding="utf-8")

    result = fresh_rc_cli._verify_guard_hash(str(tmp_path / "nonexistent.py"))
    assert result is False