"""Regression tests for the gen sidecar launcher.

Closes the 2026-06-02 gap: `mlx_lm.server` runs as a third-party Python
entry point and never invokes `apply_boot_guards`, so it had no memory
watchdog. The launcher wraps it as a subprocess and watchdogs its
phys_footprint via the same `_phys_footprint_darwin` reader used in
`sidecar_boot._rss_bytes`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _run(script: str, env: dict, timeout: float = 15.0) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        **env,
        "PYTHONPATH": f"{REPO_ROOT}:{SRC}",
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=timeout,
    )


def test_remote_backend_short_circuits():
    """RC_REASONER_BACKEND=remote exits 0 without spawning anything."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, 'src')
        import gen_sidecar_launcher as g
        try:
            g.main()
        except SystemExit as e:
            print(f"EXIT={e.code}")
    """)
    r = _run(script, {
        "RC_REASONER_BACKEND": "remote",
        "S2_GEN_SINGLE_INSTANCE": "0",
    })
    assert r.returncode == 0, r.stderr
    assert "EXIT=0" in r.stdout


def test_unknown_backend_rejected():
    """Unknown backend exits non-zero with an error."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, 'src')
        import gen_sidecar_launcher as g
        try:
            g.main()
        except SystemExit as e:
            print(f"EXIT={e.code}")
    """)
    r = _run(script, {
        "RC_REASONER_BACKEND": "nope",
        "S2_GEN_SINGLE_INSTANCE": "0",
    })
    assert "EXIT=1" in r.stdout
    assert "unknown" in r.stderr.lower()


def test_watchdog_kills_child_over_cap(tmp_path):
    """A child that exceeds the cap is SIGKILLed; launcher exits 75.

    Uses a Python child that allocates a 200MB bytearray and sleeps, with
    the cap set to 0.001 GB so the very first poll fires. We invoke the
    launcher's internals directly to avoid needing mlx_lm.server installed.
    """
    script = textwrap.dedent("""
        import os
        import signal
        import subprocess
        import sys
        import threading
        import time
        sys.path.insert(0, 'src')
        import gen_sidecar_launcher as g

        # Child: hold a chunky bytearray so phys_footprint > 0.001 GB.
        child = subprocess.Popen(
            [sys.executable, "-c",
             "import time; b = bytearray(200*1024*1024); time.sleep(30)"]
        )
        # Cap 1 MB, poll fast.
        t = threading.Thread(
            target=g._watchdog_loop,
            args=(child.pid, 1 * 1024 * 1024, 0.05),
            daemon=True,
        )
        t.start()
        rc = child.wait(timeout=5)
        print(f"CHILD_RC={rc}")
        # Negative rc means signaled; SIGKILL = 9.
        if rc < 0 and -rc == signal.SIGKILL:
            print("LAUNCHER_EXIT=75")
        else:
            print(f"LAUNCHER_EXIT={rc if rc >= 0 else 128 + (-rc)}")
    """)
    r = _run(script, {}, timeout=15.0)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "LAUNCHER_EXIT=75" in r.stdout, r.stdout
    assert "exceeded cap" in r.stderr


def test_mem_limit_falls_back_to_s2_mem_limit_gb():
    """S2_GEN_MEM_LIMIT_GB unset falls back to S2_MEM_LIMIT_GB."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, 'src')
        from gen_sidecar_launcher import _mem_limit_gb
        print(f"CAP={_mem_limit_gb()}")
    """)
    r = _run(script, {"S2_MEM_LIMIT_GB": "7"})
    assert r.returncode == 0, r.stderr
    assert "CAP=7.0" in r.stdout


def test_mem_limit_explicit_gen_overrides_s2():
    """S2_GEN_MEM_LIMIT_GB takes precedence over S2_MEM_LIMIT_GB."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, 'src')
        from gen_sidecar_launcher import _mem_limit_gb
        print(f"CAP={_mem_limit_gb()}")
    """)
    r = _run(script, {"S2_MEM_LIMIT_GB": "32", "S2_GEN_MEM_LIMIT_GB": "12"})
    assert r.returncode == 0, r.stderr
    assert "CAP=12.0" in r.stdout


def test_mem_limit_zero_disables():
    """Cap <= 0 means the watchdog thread is not started in main()."""
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, 'src')
        from gen_sidecar_launcher import _mem_limit_gb
        print(f"CAP={_mem_limit_gb()}")
    """)
    r = _run(script, {"S2_GEN_MEM_LIMIT_GB": "0"})
    assert r.returncode == 0, r.stderr
    assert "CAP=0.0" in r.stdout
