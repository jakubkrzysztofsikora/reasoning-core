"""RC-SEC-01: Kill-switch state file HMAC-SHA256 integrity tests.

Verifies that direct file edits without updating the MAC sidecar are detected
as tamper and reset to safe defaults.
"""
from __future__ import annotations

import importlib
import json
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = str(REPO_ROOT / "src" / "hooks")
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)


@pytest.fixture
def hmac_state_dir(tmp_path, monkeypatch):
    """Set up a scratch state directory with HMAC key."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("RC_STATE_DIR", str(state_dir))
    # Set HMAC key for testing
    monkeypatch.setenv("RC_HMAC_KEY", "test-hmac-key-for-testing-only")
    
    # Force module reload to pick up new env vars
    if "_kill_switches" in sys.modules:
        importlib.reload(sys.modules["_kill_switches"])
    
    return state_dir


class TestKillSwitchHmacIntegrity:
    """RC-SEC-01: HMAC verification prevents unauthorized state modifications."""

    def test_direct_edit_detected_as_tamper(self, hmac_state_dir, monkeypatch):
        """Direct JSON edit without MAC update is treated as tamper."""
        import _kill_switches as ks
        
        # First, create a valid state with MAC
        ks.set_bypass_next(True)
        
        # Verify it was set
        assert ks.snapshot().get("bypass_next") is True
        
        # Now directly edit the JSON file (simulating agent tamper)
        state_file = hmac_state_dir / "kill_switches.json"
        
        tampered_state = {"bypass_next": True, "skip_files": ["/etc/passwd"], "disable_until": None}
        state_file.write_text(json.dumps(tampered_state), encoding="utf-8")
        # Leave MAC unchanged — this is the tamper
        
        # First load detects tamper, returns {}, and resets state file
        state = ks._load()
        # After tamper detection, _audit_tamper resets the file
        # Second load should return the reset state
        reset_state = ks._load()
        assert reset_state.get("bypass_next") is False, "Tamper should reset bypass_next to False"
        assert reset_state.get("skip_files") == [], "Tamper should clear skip_files"

    def test_missing_mac_sidecar_is_tamper(self, hmac_state_dir, monkeypatch):
        """State file without MAC sidecar is treated as tamper."""
        import _kill_switches as ks
        
        # Create state file manually without MAC
        state_file = hmac_state_dir / "kill_switches.json"
        state_file.write_text(json.dumps({"bypass_next": True}), encoding="utf-8")
        
        # First load detects missing MAC, returns {}, and resets
        ks._load()
        # Second load should return reset state
        reset_state = ks._load()
        assert reset_state.get("bypass_next") is False

    def test_invalid_mac_is_tamper(self, hmac_state_dir, monkeypatch):
        """Wrong MAC value is treated as tamper."""
        import _kill_switches as ks
        
        # Create valid state
        ks.set_bypass_next(True)
        
        # Corrupt the MAC
        mac_file = hmac_state_dir / "kill_switches.json.mac"
        mac_file.write_text("invalid-mac-value", encoding="utf-8")
        
        # First load detects invalid MAC, returns {}, and resets
        ks._load()
        # Second load should return reset state
        reset_state = ks._load()
        assert reset_state.get("bypass_next") is False

    def test_skip_files_one_shot_consumption(self, hmac_state_dir, monkeypatch):
        """skip_files entries are consumed on first read."""
        import _kill_switches as ks
        
        # Add a skip file entry
        test_path = "/tmp/test-skip-file.py"
        ks.add_skip_file(test_path)
        
        # First check should return True and consume
        assert ks.is_file_skipped(test_path) is True
        
        # Second check should return False (consumed)
        assert ks.is_file_skipped(test_path) is False
        
        # State should have empty skip_files now
        state = ks.snapshot()
        assert test_path not in state.get("skip_files", [])

    def test_permissions_set_to_0600(self, hmac_state_dir, monkeypatch):
        """Both state file and MAC sidecar are chmod 0600."""
        import _kill_switches as ks
        
        # Create state which triggers _save with HMAC
        ks._save({"bypass_next": True})
        
        state_file = hmac_state_dir / "kill_switches.json"
        mac_file = hmac_state_dir / "kill_switches.json.mac"
        
        assert state_file.exists(), f"State file not created at {state_file}"
        assert mac_file.exists(), f"MAC file not created at {mac_file}"
        
        state_mode = state_file.stat().st_mode & 0o777
        mac_mode = mac_file.stat().st_mode & 0o777
        
        assert state_mode == 0o600, f"State file mode {oct(state_mode)} != 0600"
        assert mac_mode == 0o600, f"MAC file mode {oct(mac_mode)} != 0600"
