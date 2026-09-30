"""Attach the local sidecar token only to loopback score requests."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from urllib.parse import urlsplit


def keychain_account() -> str:
    """Return the stable macOS account name, independent of launchd env vars."""
    if sys.platform == "darwin":
        try:
            import pwd

            return pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError, OSError):
            pass
    return os.environ.get("USER", "")


def keychain_token() -> str:
    """Read the enforcement token, preferring the stable account and legacy null account."""
    if sys.platform != "darwin":
        return ""
    account = keychain_account()
    queries = []
    if account:
        queries.append(["security", "find-generic-password", "-s",
                        "reasoning-core-enforcement", "-a", account, "-w"])
    # Older auth-bootstrap runs may have written this service with no account.
    queries.append(["security", "find-generic-password", "-s",
                    "reasoning-core-enforcement", "-w"])
    for query in queries:
        try:
            found = subprocess.run(query, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return ""


def operator_token() -> str:
    """Resolve the explicit process token first, then the macOS Keychain credential."""
    token = os.environ.get("RC_ENFORCEMENT_TOKEN", "")
    return token or keychain_token()


def score_headers(endpoint: str) -> dict[str, str]:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {}
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return {}
    token = operator_token()
    return {"Authorization": f"Bearer {token}"} if token else {}
