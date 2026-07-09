"""Memory-watchdogged launcher for the generative critic sidecar.

Wraps an external server process (default ``mlx_lm.server``) so it gets the
same boot guards as the in-process S2 sidecar:

  * Single-instance flock (``S2_GEN_SINGLE_INSTANCE``, lock name distinct from
    the S2 sidecar's).
  * RSS / phys_footprint watchdog. On macOS this polls
    ``proc_pid_rusage(RUSAGE_INFO_V4).ri_phys_footprint`` against the *child*
    pid so MLX / Metal / IOSurface unified-memory allocations count toward the
    cap. When exceeded, the child is SIGKILLed and this launcher exits 75
    (``EX_TEMPFAIL``) so launchd / sidecar_supervisor can restart it.

Background: ``mlx_lm.server`` is a third-party Python entry point — it never
calls ``apply_boot_guards`` itself. The 2026-06-02 incident showed the gen
sidecar growing to 36 GB unchecked because nothing was watching its RSS. This
launcher closes that hole without forking mlx-lm.

Environment knobs:
  RC_GEN_PORT          — bind port (default 8766).
  RC_GEN_MODEL         — model id (default Qwen2.5-Coder-1.5B-Instruct-4bit).
  S2_GEN_MEM_LIMIT_GB  — cap; falls back to ``S2_MEM_LIMIT_GB``, then 16.
  S2_GEN_MEM_POLL_S    — watchdog poll interval seconds (default 5).
  S2_GEN_SINGLE_INSTANCE — ``0`` to disable the flock.
  RC_REASONER_BACKEND  — ``mlx``/``llama``/``remote``. Only ``mlx`` and
                        ``llama`` spawn a child here; ``remote`` exits 0.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

# Importable as ``src.gen_sidecar_launcher`` or as ``python -m`` from src/.
try:
    from . import sidecar_boot
except ImportError:  # pragma: no cover - script invocation fallback
    import sidecar_boot  # type: ignore


_DEFAULT_MEM_LIMIT_GB = 16.0
_DEFAULT_POLL_S = 5.0
_EXIT_MEMORY_EXCEEDED = 75
_LOCK_NAME = "reasoning-core-gen-sidecar.lock"


def _mem_limit_gb() -> float:
    raw = os.environ.get("S2_GEN_MEM_LIMIT_GB") or os.environ.get(
        "S2_MEM_LIMIT_GB"
    )
    if raw is None or raw == "":
        return _DEFAULT_MEM_LIMIT_GB
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_MEM_LIMIT_GB
    return max(0.0, v)


def _poll_s() -> float:
    raw = os.environ.get("S2_GEN_MEM_POLL_S")
    if raw is None or raw == "":
        return _DEFAULT_POLL_S
    try:
        v = float(raw)
    except ValueError:
        return _DEFAULT_POLL_S
    return max(0.5, v)


def _resolve_mlx_server() -> str:
    """Find ``mlx_lm.server`` on disk.

    launchd's plist environment does not include the project venv, so an
    unqualified ``mlx_lm.server`` lookup via subprocess.Popen fails with
    ENOENT. Prefer the venv co-located with this module (the supervisor's
    interpreter); fall back to PATH.
    """
    import shutil

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_candidate = os.path.join(here, ".venv", "bin", "mlx_lm.server")
    if os.access(venv_candidate, os.X_OK):
        return venv_candidate
    found = shutil.which("mlx_lm.server")
    return found or "mlx_lm.server"


def _build_cmd() -> list[str]:
    backend = os.environ.get("RC_REASONER_BACKEND", "mlx")
    port = os.environ.get("RC_GEN_PORT", "8766")
    if backend == "mlx":
        model = os.environ.get(
            "RC_GEN_MODEL", "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
        )
        return [_resolve_mlx_server(), "--model", model, "--port", port]
    if backend == "llama":
        model_path = os.environ.get(
            "RC_GEN_MODEL_PATH",
            os.path.expanduser(
                "~/.cache/reasoning-core/qwen2.5-coder-1.5b-instruct.Q4_K_M.gguf"
            ),
        )
        if not os.path.exists(model_path):
            print(
                f"ERROR: GGUF model not found at {model_path}", file=sys.stderr
            )
            sys.exit(1)
        return [
            sys.executable, "-m", "llama_cpp.server",
            "--model", model_path,
            "--port", port,
        ]
    if backend == "remote":
        print("[gen-launcher] backend=remote — no local sidecar to start.",
              file=sys.stderr)
        sys.exit(0)
    print(f"ERROR: unknown RC_REASONER_BACKEND={backend!r}", file=sys.stderr)
    sys.exit(1)


def _watchdog_loop(pid: int, limit_bytes: int, poll_s: float) -> None:
    """Poll child phys_footprint; SIGKILL it if it exceeds the cap.

    The launcher's main thread will reap the child and exit 75.
    """
    while True:
        try:
            mem = sidecar_boot._phys_footprint_darwin(pid)
        except Exception:  # noqa: BLE001
            mem = None
        # Fallback: read /proc on linux via psutil if available.
        if mem is None:
            try:
                import psutil  # type: ignore

                mem = int(psutil.Process(pid).memory_info().rss)
            except Exception:  # noqa: BLE001
                mem = 0

        if mem and mem > limit_bytes:
            msg = (
                f"FATAL: gen sidecar pid={pid} phys_footprint "
                f"{mem / 1e9:.2f} GB exceeded cap {limit_bytes / 1e9:.2f} GB; "
                f"SIGKILL"
            )
            try:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        time.sleep(poll_s)


def main() -> int:
    # Single-instance gate (separate lock name from the S2 sidecar).
    if os.environ.get("S2_GEN_SINGLE_INSTANCE", "1") != "0":
        sidecar_boot.acquire_single_instance_lock(_LOCK_NAME)

    cmd = _build_cmd()
    print(
        f"[gen-launcher] spawning {' '.join(cmd)}", file=sys.stderr, flush=True
    )

    # Inherit stdio so logs flow to whoever launched us (supervisor / launchd).
    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Propagate signals to the child for clean shutdown.
    def _forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    cap_gb = _mem_limit_gb()
    if cap_gb > 0:
        limit_bytes = int(cap_gb * (1024 ** 3))
        poll = _poll_s()
        t = threading.Thread(
            target=_watchdog_loop,
            args=(proc.pid, limit_bytes, poll),
            name="gen-sidecar-mem-watchdog",
            daemon=True,
        )
        t.start()
        print(
            f"[gen-launcher] watchdog: pid={proc.pid} cap={cap_gb:.1f} GB "
            f"poll={poll:.1f}s",
            file=sys.stderr, flush=True,
        )
    else:
        print("[gen-launcher] memory watchdog disabled (cap<=0)",
              file=sys.stderr, flush=True)

    rc = proc.wait()
    # If the watchdog killed the child via SIGKILL, surface EX_TEMPFAIL so
    # launchd / supervisor will restart us.
    if rc < 0 and -rc == signal.SIGKILL:
        return _EXIT_MEMORY_EXCEEDED
    return rc if rc >= 0 else 128 + (-rc)


if __name__ == "__main__":
    sys.exit(main())
