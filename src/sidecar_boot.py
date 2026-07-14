"""Boot-time safety guards for the sidecar process.

Two guards run before the backbone is loaded:

1. ``acquire_single_instance_lock`` -- fcntl-based exclusive lock on a per-user
   path. A second sidecar invocation exits immediately (code 75 ``EX_TEMPFAIL``)
   instead of racing the first one through ``load_backbone`` and double-allocating
   the model. Without this, two concurrent starts each consumed ~10 GB resident
   before one of them lost the port-bind race (2026-05-16 incident).

2. ``set_memory_limit_gb`` -- caps process memory. On Linux it uses
   ``RLIMIT_AS``; on macOS XNU clamps ``setrlimit`` for AS/DATA/RSS regardless
   of the value, so the function spawns a background watchdog thread that
   polls RSS via ``resource.getrusage`` and calls ``os._exit(75)`` if the cap
   is exceeded. Either way, a runaway model load (e.g. accidental fallback to
   the full HF ``Mamba-Codestral-7B`` instead of the GGUF Q4 backend) is
   killed before it pages the host into swap thrash.
"""
from __future__ import annotations

import errno
import fcntl
import logging
import os
import resource
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOCK_NAME = "reasoning-core-sidecar.lock"

# Cached libc handle for proc_pid_rusage on darwin. Module-scope so the
# handle outlives any function call — a local CDLL on darwin segfaults at
# interpreter shutdown when the symbol is still referenced.
if sys.platform == "darwin":
    import ctypes as _ct
    import ctypes.util as _ctu
    _LIBC = _ct.CDLL(_ctu.find_library("c"), use_errno=True)
    _PROC_PID_RUSAGE = _LIBC.proc_pid_rusage
    _PROC_PID_RUSAGE.argtypes = [_ct.c_int, _ct.c_int, _ct.c_void_p]
    _PROC_PID_RUSAGE.restype = _ct.c_int
else:
    _LIBC = None
    _PROC_PID_RUSAGE = None
_DEFAULT_MEM_LIMIT_GB = 16.0
_DEFAULT_WATCHDOG_POLL_S = 5.0
_EXIT_MEMORY_EXCEEDED = 75  # EX_TEMPFAIL

# Holds the lock file descriptor for the lifetime of the process. The kernel
# releases the flock when the fd is closed; keeping it module-global means the
# lock survives until ``os._exit``.
_LOCK_FD: Optional[int] = None

# Holds the watchdog thread so callers can join/inspect it in tests.
_WATCHDOG_THREAD: Optional[threading.Thread] = None


def _lock_path(name: str = _DEFAULT_LOCK_NAME) -> Path:
    base = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    uid = os.getuid()
    return base / f"{name}.{uid}"


def acquire_single_instance_lock(name: str = _DEFAULT_LOCK_NAME) -> Optional[int]:
    """Take an exclusive non-blocking flock. Exit if already held.

    Returns the fd on success, ``None`` if disabled via env. On contention this
    function calls ``sys.exit(75)`` so the second sidecar dies before any heavy
    allocation. ``75`` is ``EX_TEMPFAIL`` -- caller may retry.
    """
    global _LOCK_FD
    if os.environ.get("S2_SINGLE_INSTANCE", "1") == "0":
        logger.info("single-instance lock disabled via S2_SINGLE_INSTANCE=0")
        return None

    path = _lock_path(name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            print(
                f"ERROR: another sidecar already holds {path}; refusing to start",
                file=sys.stderr,
            )
            sys.exit(75)
        raise

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _LOCK_FD = fd
    logger.info("single-instance lock acquired path=%s pid=%d", path, os.getpid())
    return fd


def _phys_footprint_darwin(pid: Optional[int] = None) -> Optional[int]:
    """Return ``ri_phys_footprint`` (bytes) via ``proc_pid_rusage`` on macOS.

    This is what Activity Monitor's "Memory" column shows and what triggers
    OS memory pressure. It includes IOSurface / Metal / unified-memory
    backings that MLX uses for weights and KV cache — bytes invisible to
    ``ru_maxrss`` and ``TASK_BASIC_INFO.resident_size``. 2026-06-02 incident:
    sidecar hit 37 GB phys_footprint while RSS read 16 GB, so the 32 GB cap
    never fired and the host swap-thrashed.

    ``pid`` defaults to the current process. Passing a child pid lets a
    Python launcher poll an external server (e.g. ``mlx_lm.server``) and
    kill it when the cap is exceeded — same enforcement, different victim.

    Uses ``proc_pid_rusage(RUSAGE_INFO_V4)`` — stable since macOS 10.9, struct
    layout documented in ``<libproc.h>`` / ``<sys/resource.h>``. Returns None
    on syscall failure.
    """
    import ctypes

    if _PROC_PID_RUSAGE is None:
        return None
    RUSAGE_INFO_V4 = 4

    # rusage_info_v4 is 296 bytes on darwin (sys/resource.h, verified on
    # macOS 15). Allocating a smaller buffer causes the kernel to scribble
    # past it — stack corruption that segfaults at interpreter shutdown.
    # ri_phys_footprint sits at offset 72 (uint64).
    _RUSAGE_INFO_V4_SIZE = 296
    _PHYS_FOOTPRINT_OFFSET = 72
    buf = (ctypes.c_uint8 * _RUSAGE_INFO_V4_SIZE)()

    target = os.getpid() if pid is None else pid
    rc = _PROC_PID_RUSAGE(target, RUSAGE_INFO_V4, ctypes.byref(buf))
    if rc != 0:
        return None

    phys = ctypes.c_uint64.from_buffer(buf, _PHYS_FOOTPRINT_OFFSET)
    return int(phys.value)


def _rss_bytes() -> int:
    """Return CURRENT process memory in bytes (not the monotonic high-water).

    On macOS this returns ``phys_footprint`` — what Activity Monitor shows and
    what triggers OS memory pressure. This is the only value that accounts for
    MLX / Metal / IOSurface unified-memory allocations.

    On Linux this returns live RSS via psutil (or ``ru_maxrss`` × 1024 as
    fallback — note the Linux fallback is monotonic-max, not live).

    The previous implementation used ``ru_maxrss`` everywhere. That is (a)
    monotonic so freed memory never drops, and (b) on darwin counts only CPU
    resident pages, missing MLX unified-memory allocations entirely.
    """
    if sys.platform == "darwin":
        try:
            pf = _phys_footprint_darwin()
            if pf is not None:
                return pf
        except Exception:  # noqa: BLE001
            pass

    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(usage)
        return int(usage) * 1024


def _watchdog_loop(limit_bytes: int, poll_s: float) -> None:
    """Background thread: kill the process if RSS exceeds the cap."""
    while True:
        rss = _rss_bytes()
        if rss > limit_bytes:
            msg = (
                f"FATAL: sidecar RSS {rss / 1e9:.2f} GB exceeded cap "
                f"{limit_bytes / 1e9:.2f} GB; calling os._exit({_EXIT_MEMORY_EXCEEDED})"
            )
            try:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:  # noqa: BLE001
                pass
            os._exit(_EXIT_MEMORY_EXCEEDED)
        time.sleep(poll_s)


def set_memory_limit_gb(
    gb: Optional[float] = None,
    poll_s: float = _DEFAULT_WATCHDOG_POLL_S,
) -> Optional[int]:
    """Cap process memory at ``gb`` gigabytes (default ``S2_MEM_LIMIT_GB`` or 16).

    Strategy:
    * Attempt ``setrlimit(RLIMIT_AS)`` first -- works on Linux, gives the
      kernel-enforced ``MemoryError`` semantics. On macOS XNU rejects setrlimit
      for AS/DATA/RSS regardless of value, so this is best-effort.
    * Always spawn a daemon watchdog thread that polls RSS every ``poll_s``
      seconds and calls ``os._exit(75)`` when the cap is exceeded. This is
      the actual enforcement on macOS.

    A value of ``0`` (via arg or env) disables both. Returns the bytes value
    applied, or ``None`` if disabled.
    """
    global _WATCHDOG_THREAD

    if gb is None:
        raw = os.environ.get("S2_MEM_LIMIT_GB")
        if raw is None:
            gb = _DEFAULT_MEM_LIMIT_GB
        else:
            try:
                gb = float(raw)
            except ValueError:
                logger.warning(
                    "S2_MEM_LIMIT_GB=%r invalid; defaulting to %s",
                    raw, _DEFAULT_MEM_LIMIT_GB,
                )
                gb = _DEFAULT_MEM_LIMIT_GB

    if gb <= 0:
        logger.info("memory cap disabled (S2_MEM_LIMIT_GB=%s)", gb)
        return None

    limit_bytes = int(gb * (1024 ** 3))

    # Start the watchdog FIRST. On Linux, setrlimit(RLIMIT_AS) is enforced
    # immediately; if we applied the cap first, a cap below the interpreter's
    # own footprint (test uses 1 MB) would make pthread_create fail with
    # "can't start new thread" and we'd never reach os._exit(75). The
    # watchdog is the actual enforcer on macOS and belt-and-suspenders
    # elsewhere -- it must be alive before the cap is applied.
    if _WATCHDOG_THREAD is None or not _WATCHDOG_THREAD.is_alive():
        _WATCHDOG_THREAD = threading.Thread(
            target=_watchdog_loop,
            args=(limit_bytes, poll_s),
            name="sidecar-mem-watchdog",
            daemon=True,
        )
        _WATCHDOG_THREAD.start()
        logger.info(
            "memory watchdog started: cap=%.1f GB poll=%.1fs", gb, poll_s,
        )

    # Best-effort RLIMIT_AS (Linux honors it; macOS rejects).
    try:
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
        logger.info("RLIMIT_AS set to %.1f GB", gb)
    except (ValueError, OSError) as exc:
        logger.info(
            "RLIMIT_AS not enforceable on this platform (%s); "
            "using RSS watchdog instead", exc,
        )

    return limit_bytes


def apply_boot_guards() -> None:
    """Apply all sidecar boot-time guards. Call before any heavy allocation."""
    acquire_single_instance_lock()
    set_memory_limit_gb()
