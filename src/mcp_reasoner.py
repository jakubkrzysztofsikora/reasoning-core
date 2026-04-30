"""FastMCP bridge exposing the `reason_over_edit` tool.

This module is the D3 deliverable for the reasoning-core project. It bridges
Claude Code (System 1) to the local S2 sidecar (System 2) running on
127.0.0.1:8765. The sidecar contract is defined in RC-005:

- POST /score body: {"path": str, "before_src": str, "after_src": str}
- 200 -> ImpactReport JSON (architectural_impact_score, coherence_delta,
  risk_vector[8], risk_labels[8], regression_detected, human_summary)
- 415 -> {"error": "unsupported_language", "extension": ".rb"}

All errors are translated into structured response dicts; the tool handler
never raises out to the MCP runtime.

Run with: `python3 -m src.mcp_reasoner`
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Literal

import httpx
from mcp.server.fastmcp import FastMCP

SIDE_CAR_URL = os.getenv("S2_URL", "http://127.0.0.1:8765")
SCORE_ENDPOINT = f"{SIDE_CAR_URL}/score"


def _timeout_seconds() -> int:
    """Resolve the sidecar HTTP timeout from S2_TIMEOUT (default 30s)."""
    try:
        return int(os.getenv("S2_TIMEOUT", "30"))
    except ValueError:
        return 30


def _fail_closed() -> bool:
    """Return True when the sidecar should be treated as fail-closed."""
    return os.getenv("S2_FAIL_CLOSED", "0") == "1"


def _read_before_src(file_path: str, change_kind: str) -> str:
    """Best-effort read of the current on-disk contents of `file_path`.

    For `change_kind == "edit"` we expect the file to exist; if it doesn't
    we fall back to an empty string rather than raising — the sidecar can
    still score an edit-against-nothing as a creation.

    For `change_kind == "write"` a missing file is the normal case and
    yields the empty string.
    """
    path = Path(file_path)
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


mcp = FastMCP("hybrid-reasoner")


@mcp.tool()
def reason_over_edit(
    file_path: str,
    proposed_change: str,
    change_kind: Literal["edit", "write"] = "edit",
) -> Dict[str, Any]:
    """Score a proposed edit/write against the local S2 sidecar.

    Args:
        file_path: Absolute or workspace-relative path of the file being changed.
        proposed_change: The full after-state source for the file.
        change_kind: "edit" for an in-place modification, "write" for create/overwrite.

    Returns:
        A dict mirroring the sidecar ImpactReport on success. On any failure
        the dict is degraded but well-formed:
          - sidecar 415 -> {"regression_detected": False, "degraded": True,
                            "reason": "unsupported_language", "extension": "..."}
          - sidecar unreachable / timeout / 5xx -> by default fail-open
            ({"regression_detected": False, "degraded": True,
              "reason": "sidecar_unavailable"}); when S2_FAIL_CLOSED=1 the
            same condition fails closed (regression_detected: True).
          - any other unexpected error -> degraded fail-open dict.
    """
    before_src = _read_before_src(file_path, change_kind)
    after_src = proposed_change
    payload = {
        "path": file_path,
        "before_src": before_src,
        "after_src": after_src,
    }

    try:
        with httpx.Client(timeout=_timeout_seconds()) as client:
            response = client.post(SCORE_ENDPOINT, json=payload)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError):
        return _sidecar_unavailable()
    except Exception:  # noqa: BLE001 - hard guard; tool must never raise.
        return _sidecar_unavailable()

    if response.status_code == 415:
        try:
            body = response.json()
        except ValueError:
            body = {}
        extension = body.get("extension") if isinstance(body, dict) else None
        return {
            "regression_detected": False,
            "degraded": True,
            "reason": "unsupported_language",
            "extension": extension or Path(file_path).suffix,
        }

    if response.status_code != 200:
        # 4xx (including 400 invalid JSON, which we should never hit since
        # we send well-formed JSON) and 5xx alike are treated as
        # sidecar-unavailable for failure-mode purposes.
        return _sidecar_unavailable(
            extra={"http_status": response.status_code},
        )

    try:
        report = response.json()
    except ValueError:
        return _sidecar_unavailable(extra={"reason": "invalid_sidecar_json"})

    if not isinstance(report, dict):
        return _sidecar_unavailable(extra={"reason": "invalid_sidecar_json"})

    return report


def _sidecar_unavailable(extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build the degraded response for an unreachable / failing sidecar."""
    base: Dict[str, Any] = {
        "regression_detected": _fail_closed(),
        "degraded": True,
        "reason": "sidecar_unavailable",
    }
    if extra:
        # Don't let extra clobber the canonical regression_detected/degraded keys.
        for k, v in extra.items():
            if k not in ("regression_detected", "degraded"):
                base[k] = v
    return base


if __name__ == "__main__":
    mcp.run()
