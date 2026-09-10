#!/usr/bin/env python3
"""SessionStart hook (P3 Invariant 3 prep).

Snapshots the worktree's file-extension distribution + declares a dominant
language family for this session. Persists the manifest keyed by
(cwd_hash, task_spec_hash) so it survives `claude --resume`.

Honored under RC_LANG_LOCK=1. Reads CLAUDE_PROJECT_DIR for cwd; the task
spec is approximated by the first-line title from any open prompt or the
literal env var RC_TASK_SPEC if set.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _session_manifest as _sm  # type: ignore  # noqa: E402
import audit_log  # type: ignore  # noqa: E402


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _emit_session_start(
    payload: dict,
    *,
    cwd: str,
    task_spec: str,
    manifest_key: str,
    manifest: dict | None,
    reused: bool,
) -> None:
    try:
        audit_log.append_correlated_event(
            event_type="session_started",
            tool_name="SessionStart",
            decision="session_started",
            payload=payload,
            project_dir=cwd,
            manifest_key=manifest_key,
            task_spec_hash=manifest.get("task_spec_hash", "") if manifest else "",
            declared_language=manifest.get("declared_language") if manifest else None,
            language_lock_enabled=os.environ.get("RC_LANG_LOCK") == "1",
            manifest_reused=reused,
            task_spec_present=bool(task_spec),
        )
    except Exception:  # noqa: BLE001 - lifecycle telemetry must never block start
        pass


def main() -> None:
    payload = _read_payload()
    cwd = str(
        Path(
            payload.get("cwd")
            if isinstance(payload.get("cwd"), str) and payload.get("cwd")
            else os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        ).resolve()
    )
    task_spec = os.environ.get("RC_TASK_SPEC") or ""
    key = _sm.manifest_key(cwd, task_spec)

    if os.environ.get("RC_LANG_LOCK") != "1":
        _emit_session_start(
            payload,
            cwd=cwd,
            task_spec=task_spec,
            manifest_key=key,
            manifest=None,
            reused=False,
        )
        sys.exit(0)

    existing = _sm.load(key)
    if existing and (time.time() - float(existing.get("created_ts", 0))) < 86400:
        # Rehydrate: < 24h old, same task_spec_hash → keep manifest.
        _emit_session_start(
            payload,
            cwd=cwd,
            task_spec=task_spec,
            manifest_key=key,
            manifest=existing,
            reused=True,
        )
        sys.exit(0)
    declared, counts = _sm.detect_initial_language(cwd)
    manifest = {
        "key": key,
        "cwd": cwd,
        "cwd_hash": key.split("_", 1)[0] if "_" in key else key[:12],
        "task_spec_hash": key.split("_", 1)[1] if "_" in key else "",
        "created_ts": time.time(),
        "declared_language": declared,
        "framework": None,  # detection deferred
        "ext_distribution": counts,
        "lang_allow": [
            ext.strip() for ext in (os.environ.get("RC_LANG_ALLOW") or "").split(",") if ext.strip()
        ],
    }
    _sm.save(manifest)
    _emit_session_start(
        payload,
        cwd=cwd,
        task_spec=task_spec,
        manifest_key=key,
        manifest=manifest,
        reused=False,
    )
    if declared:
        sys.stderr.write(
            f"[hybrid-reasoner] session manifest: declared_language={declared}\n"
        )
    else:
        sys.stderr.write(
            "[hybrid-reasoner] session manifest: declared_language=None "
            "(low-signal worktree; lang-lock will not gate this session)\n"
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
