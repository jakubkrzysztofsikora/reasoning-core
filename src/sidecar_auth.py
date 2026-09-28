"""Attach the local sidecar token only to loopback score requests."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from urllib.parse import urlsplit


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
    token = os.environ.get("RC_ENFORCEMENT_TOKEN", "")
    if not token and sys.platform == "darwin":
        try:
            found = subprocess.run(
                ["security", "find-generic-password", "-s", "reasoning-core-enforcement",
                 "-a", os.environ.get("USER", ""), "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if found.returncode == 0:
                token = found.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"Authorization": f"Bearer {token}"} if token else {}
