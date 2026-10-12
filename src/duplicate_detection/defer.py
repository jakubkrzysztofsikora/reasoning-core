"""Defer a duplicate finding with a written reason (backs ``rc dup-defer``).

The only way past an EXTRACT block other than extracting: the agent has to say
why, and the reason stays on the finding in the ledger.
"""
from __future__ import annotations

from typing import Any

from . import findings as ledger


class DeferError(ValueError):
    """A deferral that cannot be recorded; the message says why."""


def defer(repo_root: str, finding_id: str, reason: str) -> dict[str, Any]:
    if not (reason or "").strip():
        raise DeferError("a non-empty --reason is required")
    path = ledger.ledger_path(repo_root)
    rows = ledger.load(path)
    row = rows.get(finding_id)
    if row is None:
        live = sorted(i for i, r in rows.items() if r["status"] in ("open", "surfaced"))
        raise DeferError(f"no finding {finding_id!r}; open findings: {', '.join(live) or 'none'}")
    if row["status"] == "resolved":
        raise DeferError(f"finding {finding_id} is already resolved")
    ledger.set_status(path, finding_id, "deferred", reason=reason.strip())
    return ledger.load(path)[finding_id]
