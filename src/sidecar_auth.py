"""Attach the local sidecar token only to loopback score requests."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

KEYCHAIN_LOOKUP_BUDGET_S = 5.0
KEYCHAIN_ACCOUNT_LOOKUP_MAX_S = KEYCHAIN_LOOKUP_BUDGET_S
_ACCOUNT_CACHE = ""
_ACCOUNT_LOOKUP_LOCK = threading.Lock()
_ACCOUNT_LOOKUP_THREAD: threading.Thread | None = None


def _lookup_macos_account() -> str:
    """Resolve the current uid to its local account name."""
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def keychain_account(timeout_s: float = KEYCHAIN_LOOKUP_BUDGET_S) -> str:
    """Return the stable macOS account name, independent of launchd env vars."""
    global _ACCOUNT_CACHE, _ACCOUNT_LOOKUP_THREAD
    override = os.environ.get("RC_ENFORCEMENT_KEYCHAIN_ACCOUNT", "")
    if override:
        return override
    if _ACCOUNT_CACHE:
        return _ACCOUNT_CACHE
    if sys.platform == "darwin":
        account_timeout = min(max(timeout_s, 0.0), KEYCHAIN_ACCOUNT_LOOKUP_MAX_S)
        if account_timeout > 0:
            with _ACCOUNT_LOOKUP_LOCK:
                worker = _ACCOUNT_LOOKUP_THREAD
                if worker is None or not worker.is_alive():
                    def lookup() -> None:
                        global _ACCOUNT_CACHE
                        try:
                            account = _lookup_macos_account()
                        except (ImportError, KeyError, OSError):
                            return
                        if account:
                            _ACCOUNT_CACHE = account

                    worker = threading.Thread(target=lookup, daemon=True)
                    _ACCOUNT_LOOKUP_THREAD = worker
                    try:
                        worker.start()
                    except RuntimeError:
                        _ACCOUNT_LOOKUP_THREAD = None
                        return ""
            worker.join(account_timeout)
            if _ACCOUNT_CACHE:
                return _ACCOUNT_CACHE
            if worker.is_alive():
                # Avoid writing credentials under a possibly stale launchd name.
                # A single timed-out worker is reused until the OS lookup returns.
                return ""
        # Never use launchd's potentially stale account name for Keychain access.
        return ""
    return os.environ.get("LOGNAME") or os.environ.get("USER", "")


def keychain_token(timeout_s: float = KEYCHAIN_LOOKUP_BUDGET_S) -> str:
    """Read the enforcement token, preferring the stable account and legacy null account."""
    if sys.platform != "darwin":
        return ""
    deadline = time.monotonic() + min(max(timeout_s, 0.0), KEYCHAIN_LOOKUP_BUDGET_S)
    account = keychain_account(max(0.0, deadline - time.monotonic()))
    queries = []
    if account:
        queries.append(["security", "find-generic-password", "-s",
                        "reasoning-core-enforcement", "-a", account, "-w"])
    # Older auth-bootstrap runs may have written this service with no account.
    queries.append(["security", "find-generic-password", "-s",
                    "reasoning-core-enforcement", "-a", "", "-w"])
    for query in queries:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            found = subprocess.run(query, capture_output=True, text=True, timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return ""


def _file_token() -> str:
    """Read the enforcement token from ~/.config/reasoning-core/token if present."""
    token_file = Path.home() / ".config" / "reasoning-core" / "token"
    try:
        if token_file.is_file():
            content = token_file.read_text().strip()
            if content:
                return content
    except OSError:
        pass
    return ""


def operator_token(timeout_s: float = KEYCHAIN_LOOKUP_BUDGET_S) -> str:
    """Resolve the explicit process token first, then Keychain, then config token file."""
    token = os.environ.get("RC_ENFORCEMENT_TOKEN", "")
    return token or keychain_token(timeout_s) or _file_token()


def score_headers(endpoint: str, timeout_s: float = KEYCHAIN_LOOKUP_BUDGET_S) -> dict[str, str]:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {}
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return {}
    token = operator_token(timeout_s)
    return {"Authorization": f"Bearer {token}"} if token else {}
