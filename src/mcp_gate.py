"""Synthetic PreToolUse gate for hosts without a runtime hook surface.

Phase 4: Mistral Vibe and GitHub Copilot CLI both lack the per-tool hook
events that Claude Code / Gemini CLI use. We approximate the gate by
exposing a ``gate_edit`` MCP tool and instructing the agent (via
``AGENTS.md`` / ``copilot-instructions.md``) to call it before any write.

Critical contract decisions (Phase 1a/1b reviews):
- ``gate_edit`` returns a dict — does NOT raise ``McpError`` — to match
  the existing ``reason_over_edit`` contract on the same FastMCP server.
  Two tools with opposite error contracts confuse agents.
- All scoring goes through the HTTP sidecar at ``/score``. Never duplicate
  in-process scoring (would race the audit log with two writers).
- Audit row durability: ``audit_log.append_event(..., fsync=True)`` is
  called BEFORE returning, since the host may exit before deferred I/O
  lands. The sidecar ``/score`` handler is pure compute and does not
  write the audit log.
"""

from __future__ import annotations

import ipaddress
import os
import urllib.parse
from typing import Any, Dict, Literal

import httpx

from src.hooks import _host_env, audit_log

SIDE_CAR_URL = os.getenv("S2_URL", "http://127.0.0.1:8765")
SCORE_ENDPOINT = f"{SIDE_CAR_URL}/score"


def _is_loopback_url(url: str) -> bool:
    """Return True iff url has http(s) scheme and host is loopback or 'localhost'."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_score_endpoint() -> str:
    """Re-read S2_URL at call time and validate before sending source contents.

    Refuses non-loopback URLs unless S2_ALLOW_REMOTE=1 is also set, to block
    source-code exfiltration via S2_URL hijack (env injected through .envrc,
    .mcp.json, IDE settings, or shell rc).
    """
    url = os.getenv("S2_URL", "http://127.0.0.1:8765")
    if not _is_loopback_url(url) and os.getenv("S2_ALLOW_REMOTE") != "1":
        raise ValueError(
            f"S2_URL points outside loopback ({url!r}); set S2_ALLOW_REMOTE=1 to override"
        )
    return f"{url}/score"


def _timeout_seconds() -> int:
    try:
        return int(os.getenv("S2_TIMEOUT", "30"))
    except ValueError:
        return 30


def _fail_closed() -> bool:
    return os.getenv("S2_FAIL_CLOSED", "0") == "1"


def gate_edit(
    file_path: str,
    before_src: str,
    after_src: str,
    change_kind: Literal["edit", "write"] = "edit",
) -> Dict[str, Any]:
    """MUST be called before any write/edit on hosts without runtime hooks.

    Args:
        file_path: Absolute or workspace-relative path of the file being changed.
        before_src: Current on-disk contents (empty string for a new write).
        after_src: Proposed post-edit contents.
        change_kind: "edit" or "write".

    Returns:
        Dict with ``decision`` ("blocked" | "allowed"), ``message`` (human
        summary), ``regression_detected`` (bool), and on a successful
        sidecar call, the full ``ImpactReport`` keys merged in.
    """
    body = {
        "path": file_path,
        "before_src": before_src,
        "after_src": after_src,
        "change_kind": change_kind,
        # Wire-level host attribution so the sidecar can audit per-host
        # without re-reading the env (review-driven Phase 4).
        "host": _host_label(),
        "session_id": _session_id(),
    }
    started_audit_decision: str
    started_message: str
    report: Dict[str, Any] = {}
    try:
        endpoint = _resolve_score_endpoint()
        with httpx.Client(timeout=_timeout_seconds()) as client:
            r = client.post(endpoint, json=body)
        if r.status_code == 415:
            started_audit_decision = "allowed"
            started_message = "unsupported_language"
            report = {"unsupported_language": True}
        elif r.status_code != 200:
            started_audit_decision = "blocked" if _fail_closed() else "allowed"
            started_message = f"sidecar_http_{r.status_code}"
            report = {"sidecar_unavailable": True, "status_code": r.status_code}
        else:
            try:
                report = r.json()
            except Exception:  # noqa: BLE001
                report = {}
            regression = bool(report.get("regression_detected"))
            # The sidecar's Mamba/coherence report is not a standalone policy
            # verdict. Hosts using the MCP write path receive an advisory
            # receipt unless this caller has supplied deterministic evidence.
            corroborated = bool(report.get("deterministic_signal"))
            if regression and os.environ.get("RC_NEURAL_CORROBORATED", "1") == "1" and not corroborated:
                report["neural_signal_mode"] = "advisory"
                report["regression_detected"] = False
                regression = False
            started_audit_decision = "blocked" if regression else "allowed"
            started_message = report.get("human_summary", "") or ""
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        started_audit_decision = "blocked" if _fail_closed() else "allowed"
        started_message = f"sidecar_unreachable: {exc!r}"
        report = {"sidecar_unavailable": True}
    except Exception as exc:  # noqa: BLE001
        started_audit_decision = "blocked" if _fail_closed() else "allowed"
        started_message = f"unexpected: {exc!r}"
        report = {"sidecar_unavailable": True}

    # Audit-row durability: fsync before returning. Host may exit before
    # deferred I/O lands. (Phase 4 review-driven.)
    try:
        audit_log.append_event(
            audit_log.new_event(
                tool_name="gate_edit",
                decision=started_audit_decision,
                file_path=file_path,
                reason=started_message,
                signal_source="mcp_gate",
            ),
            fsync=True,
        )
    except Exception:  # noqa: BLE001 - audit failure must not break the gate
        pass

    return {
        "decision": started_audit_decision,
        "message": started_message,
        "regression_detected": bool(report.get("regression_detected")),
        **report,
    }


def _host_label() -> str:
    try:
        return _host_env.host()
    except Exception:  # noqa: BLE001
        return "unknown"


def _session_id() -> str:
    try:
        return _host_env.session_id()
    except Exception:  # noqa: BLE001
        return ""


__all__ = ["gate_edit", "SCORE_ENDPOINT"]
