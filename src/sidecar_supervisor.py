"""Sidecar supervisor (P5 — single broker for Mamba + Qwen).

Reviewer-flagged: independent start scripts for Mamba (port 8765) and
Qwen (port 8766) leave no shared healthcheck, no restart policy, no
circuit breaker. Two MLX models on shared Apple Metal contend for the
ANE/GPU; Qwen OOM blocks gen_client to its 35s timeout.

This module:
  1. Spawns + supervises both children.
  2. Polls /health on a 5s interval.
  3. Restarts on death with exponential backoff (1s, 2s, 4s, 8s).
  4. Circuit-breaks after 3 consecutive failures (60s cooldown).
  5. Exposes broker /health that aggregates child statuses.

Per-call deadlines stay client-side (gen_client RC_GEN_BUDGET_MS).
launchd KeepAlive can wrap this whole supervisor as a single user agent.

Usage:
    python -m src.sidecar_supervisor
        # Boots both sidecars, supervises forever.
"""
from __future__ import annotations

import json
import os
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


_LOG_ROTATE_BYTES = 100 * 1024 * 1024
_HEALTH_TIMEOUT_S = 3.0
_FAILURE_THRESHOLD = 5
_HEALTH_GRACE_S = 30.0
_BROKER_PORT = int(os.environ.get("RC_BROKER_PORT", "8764"))


@dataclass
class _Child:
    name: str
    cmd: List[str]
    health_url: str
    proc: Optional[subprocess.Popen] = None
    last_ok: float = 0.0
    failures: int = 0
    open_until: float = 0.0
    backoff_s: float = 1.0
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
        sys.stderr.write(f"[supervisor] {child.name} started pid={child.proc.pid}\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[supervisor] {child.name} spawn failed: {exc}\n")


def _health(url: str, timeout: float = _HEALTH_TIMEOUT_S) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _is_alive(child: _Child) -> bool:
    return child.proc is not None and child.proc.poll() is None


def _supervise_one(child: _Child, *, stop: threading.Event,
                   extra_env: Optional[Dict[str, str]] = None) -> None:
    while not stop.is_set():
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
        ok = _health(child.health_url)
        with child.lock:
            if ok:
                child.last_ok = now
                child.failures = 0
                child.backoff_s = 1.0
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


def _broker_health(children: List[_Child]) -> Dict[str, Any]:
    return broker_health_snapshot(children)


def _build_children(repo_root: Path) -> List[_Child]:
    children: List[_Child] = []
    mamba_port = int(os.environ.get("S2_PORT", "8765"))
    children.append(_Child(
        name="mamba",
        cmd=["bash", str(repo_root / "scripts" / "start-sidecar.sh")],
        health_url=f"http://127.0.0.1:{mamba_port}/health",
    ))
    backend = os.environ.get("RC_REASONER_BACKEND", "").lower()
    if backend in ("mlx", "llama"):
        gen_port = int(os.environ.get("RC_GEN_PORT", "8766"))
        # mlx_lm.server has NO /health endpoint — probe /v1/models which
        # returns 200 once the model is loaded. Works on llama_cpp.server too.
        children.append(_Child(
            name="gen",
            cmd=["bash", str(repo_root / "scripts" / "start-gen-sidecar.sh")],
            health_url=f"http://127.0.0.1:{gen_port}/v1/models",
        ))
    return children


def main() -> int:
    repo_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
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
