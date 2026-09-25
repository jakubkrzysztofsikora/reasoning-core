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


def test_auth_bootstrap_refuses_on_linux(fresh_rc_cli, tmp_path, monkeypatch):
    """RC-SEC-02: On non-Darwin platforms, auth-bootstrap refuses self-service."""
    monkeypatch.setattr(sys, "platform", "linux")
    # Mock operator presence to pass the gate
    with patch.object(fresh_rc_cli, "_operator_present", return_value=True):
        rc = fresh_rc_cli.main(["auth-bootstrap"])
    assert rc == 1  # Refused on non-darwin


def test_auth_bootstrap_keychain_darwin(monkeypatch, tmp_path):
    """RC-SEC-02: On Darwin, store token and HMAC key in keychain."""
    monkeypatch.setattr(sys, "platform", "darwin")
    
    call_log = []
    def fake_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, returncode=0)
        if isinstance(cmd, list) and "add-generic-password" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))
    
    import rc_cli
    importlib.reload(rc_cli)
    rc = rc_cli.main(["auth-bootstrap"])
    assert rc == 0
    # Should have: sudo check + 2 keychain writes (token + HMAC)
    assert len([c for c in call_log if isinstance(c, list) and "add-generic-password" in c]) == 2


def test_operator_authenticated_env_hash_on_linux(monkeypatch, tmp_path):
    """RC-SEC-02: On Linux, authenticate via RC_ENFORCEMENT_TOKEN + RC_AUTH_TOKEN_HASH."""
    monkeypatch.setattr(sys, "platform", "linux")
    import hashlib
    token = "this-is-a-very-long-test-token-for-auth-minimum-32-chars!"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", token)
    monkeypatch.setenv("RC_AUTH_TOKEN_HASH", token_hash)
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))

    import rc_cli
    importlib.reload(rc_cli)
    assert rc_cli._operator_authenticated() is True


def test_operator_authenticated_rejects_short_token(monkeypatch, tmp_path):
    """RC-SEC-02: Tokens shorter than 32 chars are rejected."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "short")
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "events"))

    import rc_cli
    importlib.reload(rc_cli)
    assert rc_cli._operator_authenticated() is False