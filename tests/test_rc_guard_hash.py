"""Tests for rc guard-hash integrity checks."""
from __future__ import annotations

import hashlib
import importlib
import json
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

    ok, reason = fresh_rc_cli._verify_guard_hash(str(guard))
    assert ok is False
    assert reason == "mismatch"


def test_guard_hash_passes_intact(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    fresh_rc_cli._init_guard_hashes([str(guard)])

    ok, reason = fresh_rc_cli._verify_guard_hash(str(guard))
    assert ok is True
    assert reason == "match"


def test_guard_hash_returns_false_when_store_missing(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    # No store file → must return False (TAMPERED)
    ok, reason = fresh_rc_cli._verify_guard_hash(str(guard))
    assert ok is False
    assert reason == "store_missing"


def test_guard_hash_returns_false_when_file_missing_from_store(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    # Store exists but does not contain this file
    store = state_dir / "guard_hashes.json"
    store.write_text("{}", encoding="utf-8")

    ok, reason = fresh_rc_cli._verify_guard_hash(str(guard))
    assert ok is False
    assert reason == "not_registered"


def test_guard_hash_returns_false_when_store_corrupt(fresh_rc_cli, tmp_path, monkeypatch):
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    store = state_dir / "guard_hashes.json"
    store.write_text("not json", encoding="utf-8")

    ok, reason = fresh_rc_cli._verify_guard_hash(str(guard))
    assert ok is False
    assert reason == "store_corrupt"


def test_guard_hash_returns_false_when_file_missing(fresh_rc_cli, tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    store = state_dir / "guard_hashes.json"
    store.write_text("{}", encoding="utf-8")

    ok, reason = fresh_rc_cli._verify_guard_hash(str(tmp_path / "nonexistent.py"))
    assert ok is False
    assert reason == "missing_file"


def test_init_warns_on_corrupt_store_and_backs_up(fresh_rc_cli, tmp_path, monkeypatch):
    """Corrupt store must be backed up, not silently clobbered."""
    guard = tmp_path / "pre_edit_guard.py"
    guard.write_text("# original", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    store = state_dir / "guard_hashes.json"
    store.write_text("not json {{{", encoding="utf-8")

    status, warnings = fresh_rc_cli._init_guard_hashes([str(guard)])
    assert status == 0
    assert any("corrupt" in w for w in warnings)
    # Backup file should exist
    assert store.with_suffix(".jsonl.corrupt").exists()
    # New store should have the hash
    data = json.loads(store.read_text(encoding="utf-8"))
    assert str(guard.resolve()) in data


def test_init_preserves_existing_records(fresh_rc_cli, tmp_path, monkeypatch):
    """Init must not erase existing records not in the current call."""
    guard1 = tmp_path / "guard1.py"
    guard1.write_text("# 1", encoding="utf-8")
    guard2 = tmp_path / "guard2.py"
    guard2.write_text("# 2", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))

    # First init with guard1
    fresh_rc_cli._init_guard_hashes([str(guard1)])
    # Second init with guard2 — guard1 must survive
    status, warnings = fresh_rc_cli._init_guard_hashes([str(guard2)])
    assert status == 0
    data = json.loads((state_dir / "guard_hashes.json").read_text(encoding="utf-8"))
    assert str(guard1.resolve()) in data
    assert str(guard2.resolve()) in data