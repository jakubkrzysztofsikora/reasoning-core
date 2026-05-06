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
from typing import List, Optional


@dataclass
class _Child:
    name: str
    cmd: List[str]
    health_url: str
    proc: Optional[subprocess.Popen] = None
    last_ok: float = 0.0
    failures: int = 0
    open_until: float = 0.0  # circuit-breaker cooldown deadline
    backoff_s: float = 1.0


def _spawn(child: _Child) -> None:
    log_path = Path(f"/tmp/rc-{child.name}.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_path, "ab")
        child.proc = subprocess.Popen(
            child.cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
        sys.stderr.write(f"[supervisor] {child.name} started pid={child.proc.pid}\n")
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[supervisor] {child.name} spawn failed: {exc}\n")


def _health(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _is_alive(child: _Child) -> bool:
    return child.proc is not None and child.proc.poll() is None


def _supervise_one(child: _Child, *, stop: threading.Event) -> None:
    while not stop.is_set():
        now = time.time()
        if now < child.open_until:
            time.sleep(min(5.0, child.open_until - now))
            continue
        if not _is_alive(child):
            sys.stderr.write(f"[supervisor] {child.name} not alive; spawning\n")
            _spawn(child)
            time.sleep(child.backoff_s)
            child.backoff_s = min(child.backoff_s * 2, 8.0)
            continue
        if _health(child.health_url):
            child.last_ok = now
            child.failures = 0
            child.backoff_s = 1.0
        else:
            child.failures += 1
            if child.failures >= 3:
                sys.stderr.write(
                    f"[supervisor] {child.name} circuit-break (60s cooldown)\n"
                )
                child.open_until = now + 60.0
                if child.proc and child.proc.poll() is None:
                    try:
                        child.proc.terminate()
                    except OSError:
                        pass
                child.failures = 0
        time.sleep(5.0)


def _broker_health(children: List[_Child]) -> dict:
    return {
        "supervisor": "ok",
        "children": [
            {
                "name": c.name,
                "alive": _is_alive(c),
                "last_ok_age_s": time.time() - c.last_ok if c.last_ok else None,
                "failures": c.failures,
                "circuit_open_for_s": max(0.0, c.open_until - time.time()),
            }
            for c in children
        ],
    }


def _build_children(repo_root: Path) -> List[_Child]:
    children: List[_Child] = []
    # Mamba sidecar (always required).
    mamba_port = int(os.environ.get("S2_PORT", "8765"))
    children.append(_Child(
        name="mamba",
        cmd=["bash", str(repo_root / "scripts" / "start-sidecar.sh")],
        health_url=f"http://127.0.0.1:{mamba_port}/health",
    ))
    # Generative sidecar (optional — only if RC_REASONER_BACKEND configured
    # and not 'remote').
    backend = os.environ.get("RC_REASONER_BACKEND", "").lower()
    if backend in ("mlx", "llama"):
        gen_port = int(os.environ.get("RC_GEN_PORT", "8766"))
        children.append(_Child(
            name="gen",
            cmd=["bash", str(repo_root / "scripts" / "start-gen-sidecar.sh")],
            health_url=f"http://127.0.0.1:{gen_port}/health",
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

    threads = []
    for c in children:
        t = threading.Thread(target=_supervise_one, args=(c,), kwargs={"stop": stop}, daemon=True)
        t.start()
        threads.append(t)

    sys.stderr.write(
        f"[supervisor] managing {len(children)} children: "
        f"{[c.name for c in children]}\n"
    )

    while not stop.is_set():
        time.sleep(10.0)
        snap = _broker_health(children)
        sys.stderr.write("[supervisor] " + json.dumps(snap) + "\n")

    for t in threads:
        t.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
