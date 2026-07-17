"""Tests for `rc auth-bootstrap` token generation."""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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
    monkeypatch.setenv("RC_AUTH_TOKEN_FILE", str(tmp_path / "auth_token"))
    import rc_cli
    importlib.reload(rc_cli)
    return rc_cli


def test_auth_bootstrap_writes_token_file_on_linux(fresh_rc_cli, tmp_path, monkeypatch):
    """On non-Darwin platforms, write token to file with mode 0600."""
    monkeypatch.setattr(sys, "platform", "linux")
    rc = fresh_rc_cli.main(["auth-bootstrap"])
    assert rc == 0
    token_file = tmp_path / "auth_token"
    assert token_file.exists()
    token = token_file.read_text(encoding="utf-8").strip()
    assert len(token) >= 32
    # Token printed on stdout (so operator can copy it)
    # Mode 0600
    mode = token_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_auth_bootstrap_handles_existing_keychain(monkeypatch, tmp_path):
    """On Darwin, prefer keychain when available."""
    monkeypatch.setattr(sys, "platform", "darwin")

    def fake_security(*args, **kwargs):
        if "add-generic-password" in args[0]:
            return subprocess.CompletedProcess(
                args=["security"],
                returncode=0,
                stdout="",
                stderr="",
            )
        return subprocess.CompletedProcess(args=["security"], returncode=1, stdout="", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_security)
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_AUTH_TOKEN_FILE", str(tmp_path / "auth_token"))

    import rc_cli
    importlib.reload(rc_cli)
    rc = rc_cli.main(["auth-bootstrap"])
    assert rc == 0


def test_operator_authenticated_reads_token_file_on_linux(monkeypatch, tmp_path):
    """On Linux, _operator_authenticated should read from the token file."""
    monkeypatch.setattr(sys, "platform", "linux")
    token = "this-is-a-very-long-test-token-for-auth"
    token_file = tmp_path / "auth_token"
    token_file.write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("RC_AUTH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", token)
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))

    import rc_cli
    importlib.reload(rc_cli)
    assert rc_cli._operator_authenticated() is True


def test_operator_authenticated_rejects_wrong_token(monkeypatch, tmp_path):
    """Wrong token should not authenticate."""
    monkeypatch.setattr(sys, "platform", "linux")
    token_file = tmp_path / "auth_token"
    token_file.write_text("stored-token-12345678\n", encoding="utf-8")
    monkeypatch.setenv("RC_AUTH_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "wrong-token-but-long-enough")
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))

    import rc_cli
    importlib.reload(rc_cli)
    assert rc_cli._operator_authenticated() is False