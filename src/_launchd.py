"""Launchd lifecycle helpers for GUI and SSH/background sessions."""
from __future__ import annotations

import os
import plistlib
import subprocess
import time
from pathlib import Path


def restartable_plist(text: str, previous: str | None = None) -> str:
    """Preserve the supplied configuration while enabling reliable restarts."""
    config = plistlib.loads(text.encode())
    if previous is not None:
        old = plistlib.loads(previous.encode()).get("EnvironmentVariables", {})
        if old.get("HF_HOME"):
            config.setdefault("EnvironmentVariables", {})["HF_HOME"] = old["HF_HOME"]
    config["KeepAlive"] = True
    config["RunAtLoad"] = True
    config["LimitLoadToSessionType"] = ["Aqua", "Background"]
    config["ThrottleInterval"] = max(10, int(config.get("ThrottleInterval", 10)))
    return plistlib.dumps(config, sort_keys=False).decode()


def load_supervisor(plist_path: Path) -> tuple[bool, str]:
    """Bootstrap into an available domain and confirm the job is registered."""
    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, timeout=15)

    try:
        uid = os.getuid()
        gui = f"gui/{uid}"
        domain = gui if run(["launchctl", "print", gui]).returncode == 0 else f"user/{uid}"
        service = f"{domain}/com.reasoning-core.supervisor"
        enabled = run(["launchctl", "enable", service])
        if enabled.returncode != 0:
            return False, enabled.stderr.decode(errors="replace").strip()
        # A GUI login may follow an installation made over SSH.
        if domain == gui:
            run(["launchctl", "bootout", f"user/{uid}/com.reasoning-core.supervisor"])
        removed = run(["launchctl", "bootout", service])
        for attempt in range(30):
            loaded = run(["launchctl", "bootstrap", domain, str(plist_path)])
            if loaded.returncode == 0 or removed.returncode != 0 or loaded.returncode != 5:
                break
            if attempt < 29:
                time.sleep(0.5)
        if loaded.returncode != 0:
            return False, loaded.stderr.decode(errors="replace").strip()
        status = run(["launchctl", "print", service])
        if status.returncode != 0:
            return False, status.stderr.decode(errors="replace").strip()
        return True, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
