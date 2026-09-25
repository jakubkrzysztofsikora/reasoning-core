"""Kill-switch primitives. Read at hook execution time.

State file: ~/.local/state/reasoning-core/kill_switches.json
Sidecar:   ~/.local/state/reasoning-core/kill_switches.json.mac (HMAC-SHA256)
Schema:
    {
      "bypass_next": bool,            # consumed-on-read
      "skip_files": ["abs/path", ...],
      "disable_until": "<iso-ts>" | null
    }

The state file is protected by an HMAC-SHA256 sidecar keyed from the macOS
keychain service 'reasoning-core-enforcement'. On darwin the key MUST come
from the keychain; on other platforms RC_HMAC_KEY env var is accepted (CI
pre-provisioned). Tamper → reset to all-false + audit event.

Permissions: both files chmod 0600.

Env vars are fallback only (RC_BYPASS_NEXT=1).

Operator can flip switches without restarting Claude — the hook reads at
call time, not session boot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


_STATE_DIR = Path(os.environ.get(
    "RC_STATE_DIR",
    os.path.expanduser("~/.local/state/reasoning-core"),
))
_STATE_FILE = _STATE_DIR / "kill_switches.json"
_MAC_FILE = _STATE_DIR / "kill_switches.json.mac"


def _get_hmac_key() -> bytes | None:
    """Retrieve HMAC key from platform secret store.
    Order:
      1. macOS keychain service 'reasoning-core-enforcement' (darwin only).
      2. RC_HMAC_KEY env var (CI pre-provisioned).
    Returns raw key bytes or None if unavailable.
    """
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", "reasoning-core-hmac", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().encode("utf-8")
        except FileNotFoundError:
            pass
        except Exception:
            pass
    key_env = os.environ.get("RC_HMAC_KEY")
    if key_env:
        return key_env.encode("utf-8")
    return None


def _compute_mac(data: bytes, key: bytes) -> str:
    """Compute HMAC-SHA256 hex digest."""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def _verify_mac(state_bytes: bytes, mac_hex: str, key: bytes) -> bool:
    """Verify HMAC-SHA256 in constant time."""
    expected = _compute_mac(state_bytes, key)
    return hmac.compare_digest(expected, mac_hex)


def _set_permissions(path: Path) -> None:
    """Set file permissions to 0600 (owner read/write only)."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _load_raw() -> tuple[bytes | None, str | None]:
    """Read state file and MAC sidecar. Returns (state_bytes, mac_hex) or (None, None)."""
    if not _STATE_FILE.exists():
        return None, None
    try:
        state_bytes = _STATE_FILE.read_bytes()
    except OSError:
        return None, None
    mac_hex = None
    if _MAC_FILE.exists():
        try:
            mac_hex = _MAC_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return state_bytes, mac_hex


def _load() -> dict:
    """Load state with HMAC verification. Tamper → reset + audit."""
    key = _get_hmac_key()
    state_bytes, mac_hex = _load_raw()

    if state_bytes is None:
        return {}

    # If no key available, cannot verify — treat as tamper
    if key is None:
        _audit_tamper("hmac_key_unavailable")
        return {}

    # If MAC is missing, treat as tamper
    if mac_hex is None:
        _audit_tamper("missing_mac_sidecar")
        return {}

    # Verify MAC
    if not _verify_mac(state_bytes, mac_hex, key):
        _audit_tamper("invalid_mac")
        return {}

    try:
        return json.loads(state_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _audit_tamper("corrupt_json")
        return {}


def _save(state: dict) -> None:
    """Save state with HMAC sidecar. Both files chmod 0600."""
    key = _get_hmac_key()
    if key is None:
        # Cannot sign without key — save unsigned but log warning
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        _set_permissions(_STATE_FILE)
        return

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_bytes = json.dumps(state, indent=2).encode("utf-8")
    mac_hex = _compute_mac(state_bytes, key)

    _STATE_FILE.write_bytes(state_bytes)
    _MAC_FILE.write_text(mac_hex, encoding="utf-8")
    _set_permissions(_STATE_FILE)
    _set_permissions(_MAC_FILE)


def _audit_tamper(reason: str) -> None:
    """Record tamper event in audit log."""
    try:
        # Try relative import first (package context), then absolute
        try:
            from .audit_log import append_audit_row
        except ImportError:
            from audit_log import append_audit_row  # type: ignore
        append_audit_row(
            decision="tamper_detected",
            reason=f"kill_switch_state_tamper: {reason}",
            extra={"action": "reset_to_defaults"},
        )
    except Exception:
        # Audit logging failed — still reset state but don't crash
        pass
    # Reset state file to safe defaults
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    default_state = {"bypass_next": False, "skip_files": [], "disable_until": None}
    _save(default_state)


def consume_bypass_next() -> bool:
    """Return True if bypass_next is set; clear it on read.

    Also honours RC_BYPASS_NEXT=1 env (one-shot, hook process scope).
    """
    if os.environ.get("RC_BYPASS_NEXT") == "1":
        return True
    state = _load()
    if state.get("bypass_next"):
        state["bypass_next"] = False
        _save(state)
        return True
    return False


def is_file_skipped(file_path: Optional[str]) -> bool:
    """Check if file is skipped; ONE-SHOT: removes the entry on read.
    
    Under HMAC regime, a hand-edited skip_files entry is tamper (reset).
    This function consumes the entry so it cannot be reused.
    """
    if not file_path:
        return False
    state = _load()
    skip_list = state.get("skip_files") or []
    if file_path in skip_list:
        # One-shot: remove the entry
        skip_list.remove(file_path)
        state["skip_files"] = skip_list
        _save(state)
        return True
    return False


def is_disabled_globally() -> bool:
    state = _load()
    until = state.get("disable_until")
    if not until:
        return False
    # Parse as UTC. `time.mktime` interprets struct_time as LOCAL — operator in
    # non-UTC tz would get a bypass window shifted by their offset (G5).
    try:
        from datetime import datetime, timezone
        s = until[:-1] if until.endswith("Z") else until
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return time.time() < dt.timestamp()
    except (TypeError, ValueError):
        return False


def set_bypass_next(value: bool = True) -> None:
    state = _load()
    state["bypass_next"] = bool(value)
    _save(state)


def add_skip_file(path: str) -> None:
    state = _load()
    skip_list = state.get("skip_files") or []
    if path not in skip_list:
        skip_list.append(path)
    state["skip_files"] = skip_list
    _save(state)


def remove_skip_file(path: str) -> None:
    state = _load()
    skip_list = state.get("skip_files") or []
    state["skip_files"] = [p for p in skip_list if p != path]
    _save(state)


def set_disable_until(iso_ts: Optional[str]) -> None:
    state = _load()
    state["disable_until"] = iso_ts
    _save(state)


def snapshot() -> dict:
    """Return a read-only view (does NOT consume bypass_next)."""
    return dict(_load())
