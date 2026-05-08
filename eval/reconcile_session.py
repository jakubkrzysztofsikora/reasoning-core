#!/usr/bin/env python3
"""Phase 6 follow-up: session-end reconciliation of edits vs gate_edit calls.

The biggest production gap flagged in power-user review: on Copilot/Vibe
the runtime gate is the ``gate_edit`` MCP tool, enforced only by an
``AGENTS.md`` / instructions prompt. If the agent skips ``gate_edit``,
the regression lands and the post-hoc audit row never appears (because
the tool was never called) — there's no monitoring that distinguishes
"no edits this session" from "edits without gating".

This script reconciles git working-tree changes against ``gate_edit``
audit rows for a given session. Alerts when ``count(edits) >
count(gate_edit calls)`` for that ``session_id``.

Usage::

    RC_HOST=vibe SESSION_ID=<sid> python -m eval.reconcile_session

Returns 0 if reconciled (every edited file has a matching gate_edit row),
1 if there are unaudited edits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_AUDIT_ROOT = Path(os.environ.get(
    "RC_AUDIT_ROOT", os.path.expanduser("~/.local/share/reasoning-core/events"),
))


def list_edited_files(repo: Path) -> set[str]:
    """Files modified in the working tree (staged + unstaged + untracked)."""
    out = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        return set()
    edited: set[str] = set()
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().split(" -> ")[-1]
        edited.add(path)
    return edited


def list_gate_edit_files(session_id: str) -> set[str]:
    """Files with ``gate_edit`` audit rows for ``session_id``."""
    gated: set[str] = set()
    for jsonl in REPO_AUDIT_ROOT.rglob("*.jsonl"):
        for raw in jsonl.read_text().splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if row.get("tool_name") != "gate_edit":
                continue
            if row.get("session_id") != session_id:
                continue
            fp = row.get("file_path")
            if isinstance(fp, str) and fp:
                gated.add(fp)
    return gated


def main(argv: list[str]) -> int:
    session_id = os.environ.get("SESSION_ID") or os.environ.get("RC_SESSION_ID")
    if not session_id:
        sys.stderr.write("error: SESSION_ID env var required\n")
        return 2
    repo = Path(os.environ.get("RC_PROJECT_DIR", os.getcwd()))
    edited = list_edited_files(repo)
    gated = list_gate_edit_files(session_id)
    # Match by basename since edits are repo-relative and audit is absolute.
    edited_basenames = {Path(p).name for p in edited}
    gated_basenames = {Path(p).name for p in gated}
    unaudited = edited_basenames - gated_basenames
    out = {
        "session_id": session_id,
        "host": os.environ.get("RC_HOST", "unknown"),
        "edits": sorted(edited),
        "gated": sorted(gated),
        "unaudited_edits": sorted(unaudited),
        "reconciled": len(unaudited) == 0,
    }
    print(json.dumps(out, indent=2))
    if unaudited:
        sys.stderr.write(
            f"\n[reconcile] WARNING: {len(unaudited)} edited file(s) "
            f"without a gate_edit row in session {session_id}: "
            f"{sorted(unaudited)}\n"
            "  This is expected for Claude/Gemini (runtime hooks already "
            "scored the edit). For Copilot/Vibe it indicates the agent "
            "skipped the gate_edit MCP tool — investigate the diff.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
