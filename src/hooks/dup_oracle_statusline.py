"""Claude Code statusline segment for the dup-oracle.

Reads the statusline payload on stdin, looks up how many reuse advisories the
hook has raised in this session, and prints a colour-coded segment
(``🔎 dup-oracle: N flags``) -- green at 0, escalating to red as duplication
piles up. Runs every prompt, so the count refreshes live.

Only prints when the oracle is active (``RC_DUP_ORACLE=1``); otherwise stays
silent so the statusline isn't misleading. Fails open: any error -> print
nothing, never break the statusline.

Wire into ``.claude/settings.json``::

    "statusLine": {"type": "command", "command": "python3 src/hooks/dup_oracle_statusline.py"}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from src.dup_oracle_flagcount import read_count, render
except Exception:  # noqa: BLE001 - statusline must never crash
    read_count = render = None  # type: ignore


def main() -> None:
    if os.environ.get("RC_DUP_ORACLE") != "1" or render is None:
        return  # silent unless the oracle is actually on
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        session_id = payload.get("session_id") or ""
        sys.stdout.write(render(read_count(session_id)))
    except Exception:  # noqa: BLE001 - fail open: print nothing
        pass


if __name__ == "__main__":
    main()
