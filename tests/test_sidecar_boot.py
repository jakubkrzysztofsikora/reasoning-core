"""Regression tests for sidecar boot guards.

Covers the 2026-05-16 incident where two sidecars launched in parallel each
loaded the full HF ``Mamba-Codestral-7B`` (~10 GB resident each) before one
of them lost the port-bind race. The fixes:

1. ``acquire_single_instance_lock`` -- second instance exits with code 75
   before any heavy allocation.
2. ``set_memory_limit_gb`` -- RLIMIT_AS caps the address space so a runaway
   load crashes with MemoryError instead of swap-thrashing the host.
3. ``load_backbone`` strict mode -- when the operator pins ``RC_EMBEDDER``
   explicitly, registry fallback is disabled so a GGUF flake cannot silently
   promote into the 14 GB full-HF backend.
"""
from __future__ import annotations

import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _run_python(script: str, env: dict) -> subprocess.CompletedProcess:
    """Run a python snippet in a subprocess with a clean env overlay."""
    full_env = {**os.environ, **env, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=20,
    )


# ---------------------------------------------------------------------------
# acquire_single_instance_lock
# ---------------------------------------------------------------------------

def test_single_instance_lock_blocks_second_acquirer(tmp_path):
    """A second process cannot acquire the same flock; it exits 75."""
    lock_name = f"rc-test-{os.getpid()}.lock"
    env = {"TMPDIR": str(tmp_path), "S2_SINGLE_INSTANCE": "1"}

    # Holder script: take the lock, hold for 5s.
    holder_script = textwrap.dedent(f"""
        import time
        from src.sidecar_boot import acquire_single_instance_lock
        fd = acquire_single_instance_lock({lock_name!r})
        print("HOLDER_OK", fd, flush=True)
        time.sleep(5)
    """)
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **env, "PYTHONPATH": str(REPO_ROOT)},
        text=True,
    )
    try:
        # Wait for holder to acquire.
        line = holder.stdout.readline()
        assert "HOLDER_OK" in line, f"holder did not signal: {line!r}"

        # Contender script: should exit 75 immediately.
        contender_script = textwrap.dedent(f"""
            from src.sidecar_boot import acquire_single_instance_lock
            acquire_single_instance_lock({lock_name!r})
            print("UNEXPECTED_OK")
        """)
        contender = subprocess.run(
            [sys.executable, "-c", contender_script],
            capture_output=True,
            text=True,
            env={**os.environ, **env, "PYTHONPATH": str(REPO_ROOT)},
            timeout=5,
        )
        assert contender.returncode == 75, (
            f"expected exit 75, got {contender.returncode}; "
            f"stdout={contender.stdout!r} stderr={contender.stderr!r}"
        )
        assert "already holds" in contender.stderr
    finally:
        holder.terminate()
        holder.wait(timeout=3)


def test_single_instance_lock_disabled_via_env(tmp_path):
    """S2_SINGLE_INSTANCE=0 short-circuits the lock and returns None."""
    env = {"TMPDIR": str(tmp_path), "S2_SINGLE_INSTANCE": "0"}
    r = _run_python(
        "from src.sidecar_boot import acquire_single_instance_lock as a; "
        "print('RESULT', a('x.lock'))",
        env,
    )
    assert r.returncode == 0, r.stderr
    assert "RESULT None" in r.stdout


# ---------------------------------------------------------------------------
# set_memory_limit_gb
# ---------------------------------------------------------------------------

def test_memory_limit_returns_bytes_and_starts_watchdog():
    """set_memory_limit_gb returns the byte cap and starts a watchdog thread."""
    script = textwrap.dedent("""
        from src.sidecar_boot import set_memory_limit_gb, _WATCHDOG_THREAD
        import src.sidecar_boot as boot
        applied = set_memory_limit_gb(8.0, poll_s=60.0)
        t = boot._WATCHDOG_THREAD
        print(f"APPLIED={applied} ALIVE={t is not None and t.is_alive()} NAME={t.name if t else None}")
    """)
    r = _run_python(script, {"S2_MEM_LIMIT_GB": ""})
    assert r.returncode == 0, r.stderr
    expected = 8 * (1024 ** 3)
    assert f"APPLIED={expected}" in r.stdout
    assert "ALIVE=True" in r.stdout
    assert "NAME=sidecar-mem-watchdog" in r.stdout


def test_memory_limit_zero_disables():
    """S2_MEM_LIMIT_GB=0 starts no watchdog and returns None."""
    script = textwrap.dedent("""
        import src.sidecar_boot as boot
        applied = boot.set_memory_limit_gb()
        print(f"APPLIED={applied} THREAD={boot._WATCHDOG_THREAD}")
    """)
    r = _run_python(script, {"S2_MEM_LIMIT_GB": "0"})
    assert r.returncode == 0, r.stderr
    assert "APPLIED=None" in r.stdout
    assert "THREAD=None" in r.stdout


def test_memory_limit_env_override():
    """S2_MEM_LIMIT_GB env value overrides the default."""
    script = textwrap.dedent("""
        from src.sidecar_boot import set_memory_limit_gb
        print("APPLIED", set_memory_limit_gb(poll_s=60.0))
    """)
    r = _run_python(script, {"S2_MEM_LIMIT_GB": "4"})
    assert r.returncode == 0, r.stderr
    expected = 4 * (1024 ** 3)
    assert f"APPLIED {expected}" in r.stdout


def test_watchdog_kills_process_when_cap_exceeded():
    """Watchdog calls os._exit(75) when RSS exceeds the cap.

    Trigger by setting cap below current RSS so the very first poll fires.
    """
    script = textwrap.dedent("""
        import time
        from src.sidecar_boot import set_memory_limit_gb
        # Cap at 1 MB -- our interpreter is already well over this.
        set_memory_limit_gb(0.001, poll_s=0.05)
        time.sleep(2.0)
        print("UNEXPECTED_SURVIVE")
    """)
    r = _run_python(script, {"S2_MEM_LIMIT_GB": ""})
    assert r.returncode == 75, (
        f"watchdog must self-terminate with 75; got rc={r.returncode} "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "UNEXPECTED_SURVIVE" not in r.stdout
    assert "exceeded cap" in r.stderr


# ---------------------------------------------------------------------------
# ssm_backbone strict-fallback policy
# ---------------------------------------------------------------------------

def test_load_backbone_strict_when_rc_embedder_pinned(monkeypatch):
    """When RC_EMBEDDER is set explicitly and the primary fails, no fallback.

    This is the 2026-05-16 incident protection: a transient GGUF failure must
    NOT silently promote the load into ``codestral-mamba`` (full HF 14 GB).
    """
    from src import ssm_backbone

    # Force the resolved primary to fail, and prove the fallback loop is skipped.
    monkeypatch.setenv("RC_EMBEDDER", "codestral-mamba-gguf")
    monkeypatch.setattr(ssm_backbone, "_HANDLE", None)
    ssm_backbone.reset_failure_cache()

    call_log = []

    def fake_try_load(backend, device):
        call_log.append(backend.name)
        return None  # every backend "fails" to load

    monkeypatch.setattr(ssm_backbone, "_try_load_backend", fake_try_load)

    with pytest.raises(ssm_backbone.BackboneUnavailableError) as excinfo:
        ssm_backbone.load_backbone()

    # Primary was attempted; NO subsequent backend was tried.
    assert call_log == ["codestral-mamba-gguf"], (
        f"strict mode must not iterate registry fallback; got call_log={call_log}"
    )
    assert "no fallback is permitted" in str(excinfo.value)


def test_load_backbone_fallback_runs_when_rc_embedder_unset(monkeypatch):
    """When RC_EMBEDDER is NOT set, registry fallback runs (smallest first)."""
    from src import ssm_backbone

    monkeypatch.delenv("RC_EMBEDDER", raising=False)
    monkeypatch.setattr(ssm_backbone, "_HANDLE", None)
    ssm_backbone.reset_failure_cache()

    call_log = []

    def fake_try_load(backend, device):
        call_log.append(backend.name)
        return None

    monkeypatch.setattr(ssm_backbone, "_try_load_backend", fake_try_load)

    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone.load_backbone()

    # Multiple backends attempted (one primary + at least one fallback).
    assert len(call_log) > 1, (
        "fallback loop must run when RC_EMBEDDER is unset; "
        f"got call_log={call_log}"
    )


# ---------------------------------------------------------------------------
# Negative cache for backbone load failures
# ---------------------------------------------------------------------------

def test_failed_load_is_cached_within_cooldown(monkeypatch):
    """A failed load_backbone is cached so the next call within the cooldown
    raises immediately WITHOUT invoking the (expensive) backend loader.

    This is the 2026-05-17 incident protection: each retry mmaps a multi-GB
    GGUF file. Caching the failure prevents memory exhaustion under request
    load.
    """
    from src import ssm_backbone

    monkeypatch.setenv("RC_EMBEDDER", "codestral-mamba-gguf")
    monkeypatch.setenv("S2_BACKBONE_FAIL_COOLDOWN_S", "60")
    monkeypatch.setattr(ssm_backbone, "_HANDLE", None)
    ssm_backbone.reset_failure_cache()

    call_log = []

    def fake_try_load(backend, device):
        call_log.append(backend.name)
        return None

    monkeypatch.setattr(ssm_backbone, "_try_load_backend", fake_try_load)

    # First call: fails, populates cache.
    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone.load_backbone()
    n_after_first = len(call_log)
    assert n_after_first >= 1

    # Second call within cooldown: must NOT re-invoke the loader.
    with pytest.raises(ssm_backbone.BackboneUnavailableError) as excinfo:
        ssm_backbone.load_backbone()
    assert len(call_log) == n_after_first, (
        f"loader was re-invoked despite cached failure; "
        f"calls went from {n_after_first} to {len(call_log)}"
    )
    assert "cached failure" in str(excinfo.value)


def test_failure_cache_expires_after_cooldown(monkeypatch):
    """After the cooldown elapses, the next call retries the loader."""
    from src import ssm_backbone

    monkeypatch.setenv("RC_EMBEDDER", "codestral-mamba-gguf")
    # Tiny cooldown so the test runs fast.
    monkeypatch.setenv("S2_BACKBONE_FAIL_COOLDOWN_S", "0.1")
    monkeypatch.setattr(ssm_backbone, "_HANDLE", None)
    ssm_backbone.reset_failure_cache()

    call_log = []
    monkeypatch.setattr(
        ssm_backbone, "_try_load_backend",
        lambda b, d: call_log.append(b.name),
    )

    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone.load_backbone()
    first_calls = len(call_log)

    import time
    time.sleep(0.2)

    with pytest.raises(ssm_backbone.BackboneUnavailableError):
        ssm_backbone.load_backbone()
    assert len(call_log) > first_calls, (
        "loader should re-run after cooldown expires; "
        f"calls stayed at {first_calls}"
    )


def test_failure_cache_disabled_when_cooldown_zero(monkeypatch):
    """S2_BACKBONE_FAIL_COOLDOWN_S=0 disables the negative cache."""
    from src import ssm_backbone

    monkeypatch.setenv("RC_EMBEDDER", "codestral-mamba-gguf")
    monkeypatch.setenv("S2_BACKBONE_FAIL_COOLDOWN_S", "0")
    monkeypatch.setattr(ssm_backbone, "_HANDLE", None)
    ssm_backbone.reset_failure_cache()

    call_log = []
    monkeypatch.setattr(
        ssm_backbone, "_try_load_backend",
        lambda b, d: call_log.append(b.name),
    )

    for _ in range(3):
        with pytest.raises(ssm_backbone.BackboneUnavailableError):
            ssm_backbone.load_backbone()

    assert len(call_log) >= 3, (
        f"every call should hit the loader when cooldown=0; got {len(call_log)}"
    )
