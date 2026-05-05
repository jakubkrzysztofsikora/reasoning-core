"""Kill-switch primitives. Read at hook execution time.

State file: ~/.local/state/reasoning-core/kill_switches.json
Schema:
    {
      "bypass_next": bool,            # consumed-on-read
      "skip_files": ["abs/path", ...],
      "disable_until": "<iso-ts>" | null
    }

Env vars are fallback only (RC_BYPASS_NEXT=1).

Operator can flip switches without restarting Claude — the hook reads at
call time, not session boot.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional


_STATE_DIR = Path(os.environ.get(
    "RC_STATE_DIR",
    os.path.expanduser("~/.local/state/reasoning-core"),
))
_STATE_FILE = _STATE_DIR / "kill_switches.json"


def _load() -> dict:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {}


def _save(state: dict) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def consume_bypass_next() -> bool:
    """Return True if bypass_next is set; clear it on read.

    Also honours RC_BYPASS_NEXT=1 env (one-shot, hook process scope).
    """
    if os.environ.get("RC_BYPASS_NEXT") == "1":
        return True
    state = _load()
    if state.get("bypass_next"):
        state["bypass_next"] = False
        _save(state)
        return True
    return False


def is_file_skipped(file_path: Optional[str]) -> bool:
    if not file_path:
        return False
    state = _load()
    skip_list = state.get("skip_files") or []
    return file_path in skip_list


def is_disabled_globally() -> bool:
    state = _load()
    until = state.get("disable_until")
    if not until:
        return False
    try:
        return time.time() < time.mktime(time.strptime(until, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return False


def set_bypass_next(value: bool = True) -> None:
    state = _load()
    state["bypass_next"] = bool(value)
    _save(state)


def add_skip_file(path: str) -> None:
    state = _load()
    skip_list = state.get("skip_files") or []
    if path not in skip_list:
        skip_list.append(path)
    state["skip_files"] = skip_list
    _save(state)


def remove_skip_file(path: str) -> None:
    state = _load()
    skip_list = state.get("skip_files") or []
    state["skip_files"] = [p for p in skip_list if p != path]
    _save(state)


def set_disable_until(iso_ts: Optional[str]) -> None:
    state = _load()
    state["disable_until"] = iso_ts
    _save(state)


def snapshot() -> dict:
    """Return a read-only view (does NOT consume bypass_next)."""
    return dict(_load())
