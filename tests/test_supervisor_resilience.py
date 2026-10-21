"""Tests for sidecar supervisor resilience and orphan cleanup.

Verifies safe logging under lost stderr, orphan lock reaping, and spawn circuit breaks.
"""
from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import sidecar_supervisor as supervisor  # noqa: E402


def test_safe_log_handles_oserror(monkeypatch):
    mock_stderr = Mock()
    mock_stderr.write.side_effect = OSError("lost sys.stderr")
    monkeypatch.setattr(supervisor.sys, "stderr", mock_stderr)

    # Must not raise
    supervisor._safe_log("test message\n")
    mock_stderr.write.assert_called_once_with("test message\n")


def test_cleanup_orphaned_lock_no_lock_name():
    child = supervisor._Child(
        name="test",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
        lock_name=None,
    )
    # No action if lock_name is None
    supervisor._cleanup_orphaned_lock(child)


def test_cleanup_orphaned_lock_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "_lock_path", lambda _name: tmp_path / "nonexistent.lock")
    child = supervisor._Child(
        name="test",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
        lock_name="test.lock",
    )
    supervisor._cleanup_orphaned_lock(child)


def test_cleanup_orphaned_lock_dead_pid(monkeypatch, tmp_path):
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("9999999\n")
    monkeypatch.setattr(supervisor, "_lock_path", lambda _name: lock_file)

    child = supervisor._Child(
        name="test",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
        lock_name="test.lock",
    )
    supervisor._cleanup_orphaned_lock(child)


def test_cleanup_orphaned_lock_skips_unrelated_process(monkeypatch, tmp_path):
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("12345\n")
    monkeypatch.setattr(supervisor, "_lock_path", lambda _name: lock_file)
    monkeypatch.setattr(supervisor.os, "kill", lambda _pid, _sig: None)

    # Return unrelated cmdline
    mock_run = Mock()
    mock_run.return_value.stdout = "/usr/bin/some_other_daemon\n"
    monkeypatch.setattr(supervisor.subprocess, "run", mock_run)

    killpg_mock = Mock()
    monkeypatch.setattr(supervisor.os, "killpg", killpg_mock)

    child = supervisor._Child(
        name="test",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
        lock_name="test.lock",
    )
    supervisor._cleanup_orphaned_lock(child)
    killpg_mock.assert_not_called()


def test_cleanup_orphaned_lock_reaps_sidecar_process(monkeypatch, tmp_path):
    lock_file = tmp_path / "test.lock"
    lock_file.write_text("12345\n")
    monkeypatch.setattr(supervisor, "_lock_path", lambda _name: lock_file)

    alive = [True]

    def mock_kill(pid, sig):
        if sig == 0:
            if not alive[0]:
                raise OSError("No such process")
            return None
        return None

    monkeypatch.setattr(supervisor.os, "kill", mock_kill)

    mock_run = Mock()
    mock_run.return_value.stdout = "python3 -m src.s2_core --port 8765 reasoning-core-sidecar\n"
    monkeypatch.setattr(supervisor.subprocess, "run", mock_run)

    def mock_killpg(pid, sig):
        if sig in (signal.SIGTERM, signal.SIGKILL):
            alive[0] = False

    monkeypatch.setattr(supervisor.os, "killpg", mock_killpg)

    child = supervisor._Child(
        name="mamba",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
        lock_name="test.lock",
    )
    supervisor._cleanup_orphaned_lock(child)
    assert alive[0] is False


def test_spawn_failure_increments_failures_and_backs_off(monkeypatch):
    child = supervisor._Child(
        name="test",
        cmd=["echo"],
        health_url="http://127.0.0.1:8765/health",
    )
    stop = threading.Event()

    # Mock _spawn to simulate an immediate failure/exit
    spawn_count = 0

    def mock_spawn(c, extra_env=None):
        nonlocal spawn_count
        spawn_count += 1
        proc = Mock()
        proc.poll.return_value = 75  # EX_TEMPFAIL
        c.proc = proc

    monkeypatch.setattr(supervisor, "_spawn", mock_spawn)
    monkeypatch.setattr(supervisor, "_cleanup_orphaned_lock", lambda _c: None)

    # Let the loop run through iterations by mocking stop.wait
    waits = []

    def mock_wait(timeout):
        waits.append(timeout)
        if child.open_until > 0 or len(waits) >= 20:
            stop.set()
        return True

    monkeypatch.setattr(stop, "wait", mock_wait)

    supervisor._supervise_one(child, stop=stop)

    # Should have backed off exponentially and tripped circuit breaker
    assert spawn_count >= 5
    assert child.open_until > 0  # Circuit breaker tripped
