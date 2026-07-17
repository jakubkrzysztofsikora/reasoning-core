"""Sidecar supervisor (P5 — single broker for Mamba + Qwen).

Reviewer-flagged: independent start scripts for Mamba (port 8765) and
Qwen (port 8766) leave no shared healthcheck, no restart policy, no
circuit breaker. Two MLX models on shared Apple Metal contend for the
ANE/GPU; Qwen OOM blocks gen_client to its 35s timeout.

This module:
  1. Spawns + supervises both children.
  2. Polls /health on a 5s interval.
  3. Restarts on death with exponential backoff (1s, 2s, 4s, 8s).
  4. Circuit-breaks after 5 consecutive failures (60s cooldown).
  5. Exposes broker /health that aggregates child statuses.

Per-call deadlines stay client-side (gen_client RC_GEN_BUDGET_MS).
launchd KeepAlive can wrap this whole supervisor as a single user agent.

Usage:
    python -m src.sidecar_supervisor
        # Boots both sidecars, supervises forever.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import traceback
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from _supervisor_env import build_child_env, gen_extra_env
from _supervisor_broker import broker_health_snapshot, start_broker_http
from _supervisor_recalibrate import recalibrate_watcher


_LOG_ROTATE_BYTES = 100 * 1024 * 1024
_HEALTH_TIMEOUT_S = 3.0
_FAILURE_THRESHOLD = int(os.environ.get("S2_FAILURE_THRESHOLD", "5"))
_HEALTH_GRACE_S = float(os.environ.get("S2_HEALTH_GRACE_S", "30.0"))
_BROKER_PORT = int(os.environ.get("RC_BROKER_PORT", "8764"))


def _parse_non_negative_float(var: str, default: float) -> float:
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{var}={raw!r} is not a valid number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{var}={raw!r} must be a non-negative finite number")
    return value


@dataclass
class _Child:
    name: str
    cmd: List[str]
    health_url: str
    startup_grace_s: float = 0.0
    requires_model_loaded: bool = False
    proc: Optional[subprocess.Popen] = None
    last_ok: float = 0.0
    failures: int = 0
    open_until: float = 0.0
    backoff_s: float = 1.0
    startup_grace_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _LOG_ROTATE_BYTES:
            rotated = path.with_suffix(path.suffix + ".1")
            try:
                if rotated.exists():
                    rotated.unlink()
            except OSError:
                pass
            path.rename(rotated)
    except OSError:
        pass


def _spawn(child: _Child, *, extra_env: Optional[Dict[str, str]] = None) -> None:
    log_path = Path(f"/tmp/rc-{child.name}.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log(log_path)
        with open(log_path, "ab") as f:
            child.proc = subprocess.Popen(
                child.cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=build_child_env(extra_env),
            )
        with child.lock:
            child.startup_grace_until = time.time() + child.startup_grace_s
        sys.stderr.write(f"[supervisor] {child.name} started pid={child.proc.pid}\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[supervisor] {child.name} spawn failed: {exc}\n")


def _health(
    url: str,
    timeout: float = _HEALTH_TIMEOUT_S,
    *,
    require_model_loaded: bool = False,
) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if not (200 <= resp.status < 300):
                return False
            if not require_model_loaded:
                return True
            body = resp.read()
            if not body:
                return False
            data = json.loads(body.decode("utf-8", errors="replace"))
            return bool(data.get("model_loaded"))
    except (
        urllib.error.URLError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        http.client.HTTPException,
    ):
        return False


def _is_alive(child: _Child) -> bool:
    return child.proc is not None and child.proc.poll() is None


def _supervise_one(child: _Child, *, stop: threading.Event,
                   extra_env: Optional[Dict[str, str]] = None) -> None:
    while not stop.is_set():
        try:
            now = time.time()
            with child.lock:
                cooldown_left = child.open_until - now
            if cooldown_left > 0:
                stop.wait(min(5.0, cooldown_left))
                continue
            if not _is_alive(child):
                sys.stderr.write(f"[supervisor] {child.name} not alive; spawning\n")
                _spawn(child, extra_env=extra_env)
                with child.lock:
                    wait_s = child.backoff_s
                    child.backoff_s = min(child.backoff_s * 2, 8.0)
                stop.wait(wait_s)
                continue

            # Startup grace: the mamba sidecar downloads/loads a model before the
            # HTTP server reports model_loaded:true. Health failures are ignored
            # until the first successful check or until the grace expires.
            with child.lock:
                startup_left = child.startup_grace_until - now
            if startup_left > 0:
                ok = _health(
                    child.health_url,
                    require_model_loaded=child.requires_model_loaded,
                )
                if ok:
                    with child.lock:
                        child.last_ok = now
                        child.failures = 0
                        child.backoff_s = 1.0
                        child.startup_grace_until = 0.0
                    stop.wait(5.0)
                    continue
                stop.wait(min(5.0, startup_left))
                continue

            ok = _health(
                child.health_url,
                require_model_loaded=child.requires_model_loaded,
            )
            with child.lock:
                if ok:
                    child.last_ok = now
                    child.failures = 0
                    child.backoff_s = 1.0
                    child.startup_grace_until = 0.0
                elif now - child.last_ok < _HEALTH_GRACE_S and child.last_ok > 0:
                    pass  # transient inference-load latency; ignore
                else:
                    child.failures += 1
                    if child.failures >= _FAILURE_THRESHOLD:
                        sys.stderr.write(
                            f"[supervisor] {child.name} circuit-break "
                            f"({_FAILURE_THRESHOLD} consec, 60s cooldown)\n"
                        )
                        child.open_until = now + 60.0
                        child.failures = 0
                        child.backoff_s = 1.0
                        if child.proc and child.proc.poll() is None:
                            try:
                                child.proc.terminate()
                            except OSError:
                                pass
            stop.wait(5.0)
        except Exception as exc:
            sys.stderr.write(
                f"[supervisor] {child.name} supervise loop error: {exc}\n"
                f"{traceback.format_exc()}"
            )
            stop.wait(5.0)


def _broker_health(children: List[_Child]) -> Dict[str, Any]:
    return broker_health_snapshot(children)


def _resolve_repo_root() -> Path:
    """Resolve repo root and verify it matches the supervisor's own repo.

    Defends against CLAUDE_PROJECT_DIR pointing at an attacker-controlled dir
    with a malicious scripts/start-sidecar.sh. The resolved root must be the
    directory that contains this supervisor module (i.e. the repo root).
    """
    expected = Path(__file__).resolve(strict=True).parent.parent
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    repo_root = Path(raw).resolve(strict=True)
    self_path = Path(__file__).resolve(strict=True)
    try:
        self_path.relative_to(repo_root)
    except ValueError:
        raise RuntimeError(
            f"[supervisor] CLAUDE_PROJECT_DIR={raw!r} resolves to {repo_root}, "
            f"but supervisor module {self_path} is not inside it. Refusing to spawn."
        )
    if repo_root != expected:
        raise RuntimeError(
            f"[supervisor] resolved repo_root {repo_root} does not match "
            f"expected {expected}; refusing to spawn."
        )
    if not (repo_root / "pyproject.toml").is_file():
        raise RuntimeError(f"[supervisor] {repo_root} missing pyproject.toml marker")
    return repo_root


def _build_children(repo_root: Path) -> List[_Child]:
    children: List[_Child] = []
    mamba_port = int(os.environ.get("S2_PORT", "8765"))
    mamba_startup_grace = _parse_non_negative_float("S2_MAMBA_STARTUP_GRACE_S", 1800.0)
    children.append(_Child(
        name="mamba",
        cmd=["bash", str(repo_root / "scripts" / "start-sidecar.sh")],
        health_url=f"http://127.0.0.1:{mamba_port}/health",
        startup_grace_s=mamba_startup_grace,
        requires_model_loaded=True,
    ))
    backend = os.environ.get("RC_REASONER_BACKEND", "").lower()
    if backend in ("mlx", "llama"):
        gen_port = int(os.environ.get("RC_GEN_PORT", "8766"))
        # mlx_lm.server has NO /health endpoint — probe /v1/models which
        # returns 200 once the model is loaded. Works on llama_cpp.server too.
        children.append(_Child(
            name="gen",
            # Route through gen_sidecar_launcher so MLX / Metal allocations
            # are watchdogged against S2_GEN_MEM_LIMIT_GB (falls back to
            # S2_MEM_LIMIT_GB). Shell script kept as a manual entry point but
            # no longer the supervisor-managed path. 2026-06-02 incident fix.
            cmd=[sys.executable, "-m", "src.gen_sidecar_launcher"],
            health_url=f"http://127.0.0.1:{gen_port}/v1/models",
        ))
    return children


def main() -> int:
    repo_root = _resolve_repo_root()
    children = _build_children(repo_root)
    stop = threading.Event()

    def _on_signal(signum, _frame) -> None:
        sys.stderr.write(f"[supervisor] caught signal {signum}, shutting down\n")
        stop.set()
        for c in children:
            if c.proc and c.proc.poll() is None:
                try:
                    c.proc.terminate()
                except OSError:
                    pass

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        broker = start_broker_http(children, _BROKER_PORT)
    except OSError as exc:
        sys.stderr.write(f"[supervisor] broker http bind failed: {exc}\n")
        broker = None

    threads = []
    for c in children:
        extra = gen_extra_env() if c.name == "gen" else None
        t = threading.Thread(
            target=_supervise_one,
            args=(c,),
            kwargs={"stop": stop, "extra_env": extra},
            daemon=True,
        )
        t.start()
        threads.append(t)

    recal_t = threading.Thread(
        target=recalibrate_watcher,
        args=(stop, repo_root),
        daemon=True,
        name="recalibrate-watcher",
    )
    recal_t.start()
    threads.append(recal_t)

    sys.stderr.write(
        f"[supervisor] managing {len(children)} children: "
        f"{[c.name for c in children]}\n"
    )

    while not stop.is_set():
        stop.wait(10.0)
        if not stop.is_set():
            sys.stderr.write("[supervisor] " + json.dumps(_broker_health(children)) + "\n")

    for t in threads:
        t.join(timeout=2.0)
    if broker is not None:
        broker.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
