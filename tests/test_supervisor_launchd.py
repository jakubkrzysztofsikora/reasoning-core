"""Launchd supervisor recovery contracts."""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from src import _init


def test_checkout_plist_supports_background_and_clean_exit(monkeypatch):
    root = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(_init._install_paths, "framework_root", lambda: root)
    config = plistlib.loads(_init._render_launchd_plist("/usr/bin/python3").encode())
    assert config["KeepAlive"] is True
    assert set(config["LimitLoadToSessionType"]) == {"Aqua", "Background"}
    assert config["RunAtLoad"] is True
    assert config["ThrottleInterval"] >= 10


def test_wheel_plist_supports_background_and_clean_exit(monkeypatch):
    monkeypatch.setattr(_init._install_paths, "framework_root", lambda: None)
    config = plistlib.loads(_init._render_launchd_plist("/usr/bin/python3").encode())
    assert config["KeepAlive"] is True
    assert set(config["LimitLoadToSessionType"]) == {"Aqua", "Background"}
    assert config["ProgramArguments"][0] == "/usr/bin/python3"


def test_bootstrap_falls_back_to_background_and_checks_service(monkeypatch, tmp_path):
    from src._launchd import load_supervisor
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["launchctl", "print", "gui/501"]:
            return subprocess.CompletedProcess(args, 125, b"", b"no GUI")
        if args[:3] == ["launchctl", "print", "user/501/com.reasoning-core.supervisor"]:
            return subprocess.CompletedProcess(args, 0, b"state = running", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr("src._launchd.subprocess.run", run)
    monkeypatch.setattr("src._launchd.os.getuid", lambda: 501)
    assert load_supervisor(tmp_path / "supervisor.plist") == (True, "")
    assert ["launchctl", "bootstrap", "user/501", str(tmp_path / "supervisor.plist")] in calls
    assert calls[-1] == ["launchctl", "print", "user/501/com.reasoning-core.supervisor"]


def test_bootstrap_does_not_report_success_for_missing_service(monkeypatch, tmp_path):
    from src._launchd import load_supervisor

    def run(args, **kwargs):
        if args[1] == "print":
            return subprocess.CompletedProcess(args, 113, b"", b"not found")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr("src._launchd.subprocess.run", run)
    monkeypatch.setattr("src._launchd.os.getuid", lambda: 501)
    ok, reason = load_supervisor(tmp_path / "supervisor.plist")
    assert not ok
    assert "not found" in reason


def test_bootstrap_uses_gui_when_available_and_reports_failure(monkeypatch, tmp_path):
    from src._launchd import load_supervisor
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1] == "bootstrap":
            return subprocess.CompletedProcess(args, 5, b"", b"bootstrap failed")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr("src._launchd.subprocess.run", run)
    monkeypatch.setattr("src._launchd.os.getuid", lambda: 501)
    ok, reason = load_supervisor(tmp_path / "supervisor.plist")
    assert not ok
    assert "bootstrap failed" in reason
    assert ["launchctl", "bootstrap", "gui/501", str(tmp_path / "supervisor.plist")] in calls


def test_restart_policy_preserves_existing_cache_override():
    from src._launchd import restartable_plist
    fresh = plistlib.dumps({"EnvironmentVariables": {"HF_HOME": "/Volumes/models"}}).decode()
    previous = plistlib.dumps({"EnvironmentVariables": {"HF_HOME": "/local/cache"}}).decode()
    result = plistlib.loads(restartable_plist(fresh, previous).encode())
    assert result["EnvironmentVariables"]["HF_HOME"] == "/local/cache"


def test_bootstrap_retries_while_previous_job_is_removing(monkeypatch, tmp_path):
    from src._launchd import load_supervisor
    attempts = []

    def run(args, **kwargs):
        if args[:3] == ["launchctl", "print", "gui/501"]:
            return subprocess.CompletedProcess(args, 125, b"", b"no GUI")
        if args[1] == "bootstrap":
            attempts.append(args)
            code = 5 if len(attempts) == 1 else 0
            return subprocess.CompletedProcess(args, code, b"", b"removing old job")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr("src._launchd.subprocess.run", run)
    monkeypatch.setattr("src._launchd.os.getuid", lambda: 501)
    monkeypatch.setattr("src._launchd.time.sleep", lambda _: None)
    assert load_supervisor(tmp_path / "supervisor.plist") == (True, "")
    assert len(attempts) == 2


def test_wheel_supervisor_child_uses_installed_interpreter(tmp_path):
    import sys
    from src.sidecar_supervisor import _build_children
    child = _build_children(tmp_path)[0]
    assert child.cmd == [sys.executable, "-m", "src.s2_core"]
