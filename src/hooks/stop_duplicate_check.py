#!/usr/bin/env python3
"""Stop hook: don't let the session end with an open EXTRACT duplicate.

Opt-in via ``RC_DUP_ORACLE=1`` (same switch as the per-edit check). Reads the
per-repo findings ledger written by ``post_edit_duplicate_check.py`` and hands
it to ``duplicate_detection.review.stop_decision``, which re-checks every open
finding deterministically (tree-sitter + logic-token ratio, no model).

  - An open EXTRACT finding (>= 3 sites, non-trivial, test-covered) blocks the
    stop. The agent clears it by extracting the shared function or by
    ``rc dup-defer <id> --reason "..."``. NOTE findings ride along in the same
    message and never block on their own.
  - Blocks continue while ``stop_hook_active`` is set -- otherwise one more
    stop would skip the rule -- but are capped per finding
    (``review.MAX_BLOCKS``); past the cap the stop is let through and an
    ``escalated`` audit event is written.
  - Blocks whenever the feature is on. Unlike ``stop_reconcile.py`` it does not
    wait for ``RC_MODE=copilot``: an explicit ``RC_MODE=advise`` turns blocking
    off (findings then go to stderr and the audit log only).

Fails open: any error approves the stop, with a stderr line and a
``fail-open`` audit event. One ``dup_stop`` audit event per run.

Protocol (Claude Code Stop hook): stdin JSON with ``cwd`` and
``stop_hook_active``; to block, stdout ``{"decision": "block", "reason": ...}``
(the reason is shown to the agent), exit 0.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_IMPORT_ERROR: Optional[str] = None
try:  # a hook must never crash at import
    from src.duplicate_detection import findings as ledger
    from src.duplicate_detection.review import stop_decision
except Exception as import_error:  # noqa: BLE001 - hook must never crash
    _IMPORT_ERROR = repr(import_error)
    ledger = stop_decision = None  # type: ignore


def _read_payload() -> Dict[str, Any]:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError) as err:
        # Unreadable or non-JSON stdin: fall back to the current directory, but say so.
        _warn_stderr(f"could not read the hook payload ({err!r}); using the current directory")
        return {}


def _warn_stderr(msg: str) -> None:
    try:
        print(f"dup-oracle: {msg}", file=sys.stderr)
    except (OSError, ValueError):  # silent-ok: stderr itself is unavailable; nowhere left to report
        pass


def _audit(payload: Dict[str, Any], decision: str, started: float, **fields: Any) -> None:
    try:
        from src.hooks import audit_log

        audit_log.append_correlated_event(
            event_type="dup_stop",
            tool_name="Stop",
            decision=decision,
            payload=payload,
            project_dir=payload.get("cwd"),
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_hook_active=bool(payload.get("stop_hook_active")),
            **fields,
        )
    except Exception as exc:  # noqa: BLE001
        _warn_stderr(f"audit event not written: {exc!r}")


def main() -> None:
    if os.environ.get("RC_DUP_ORACLE") != "1":
        sys.exit(0)
    started = time.monotonic()
    payload: Dict[str, Any] = {}
    if stop_decision is None or ledger is None:
        msg = f"enabled (RC_DUP_ORACLE=1) but inactive — import failed: {_IMPORT_ERROR}"
        _warn_stderr(msg)
        _audit(payload, "fail-open", started, error=msg)
        sys.exit(0)
    try:
        payload = _read_payload()
        repo_root = payload.get("cwd") or os.getcwd()
        advise_mode = os.environ.get("RC_MODE", "").strip().lower() == "advise"
        decision = stop_decision(repo_root, ledger.ledger_path(repo_root), count_blocks=not advise_mode)

        fields = dict(
            extract_ids=[f["id"] for f in decision.extract],
            note_count=len(decision.notes),
            escalated_ids=decision.escalated,
            resolved_ids=decision.resolved,
        )

        if decision.escalated:
            _warn_stderr(f"EXTRACT findings still open after the block cap: {decision.escalated}")
        if not decision.block:
            _audit(payload, "escalated" if decision.escalated else "approved", started, **fields)
        elif advise_mode:
            _warn_stderr("advise mode, not blocking:\n" + decision.message)
            _audit(payload, "advise", started, **fields)
        else:
            json.dump({"decision": "block", "reason": decision.message}, sys.stdout)
            _audit(payload, "blocked", started, **fields)
    except Exception as exc:  # noqa: BLE001
        # Fail open -- a broken check must not trap the session -- but say so.
        _warn_stderr(f"session-end duplicate check skipped (fail-open): {exc!r}")
        _audit(payload, "fail-open", started, error=repr(exc))
    sys.exit(0)


if __name__ == "__main__":
    main()
