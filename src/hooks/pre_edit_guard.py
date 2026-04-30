#!/usr/bin/env python3
"""Claude Code PreToolUse hook that gates Edit / Write / MultiEdit on the S2 sidecar.

Wired by `.claude/settings.json` (matcher `Edit|Write|MultiEdit`). The hook
reads the Claude Code PreToolUse JSON payload from stdin, calls the local
sidecar (POST http://127.0.0.1:8765/score), and:

  - exits 0 silently when no regression is detected,
  - exits 2 with a multi-line stderr block when a regression is detected,
  - exits 0 with a one-line stderr note for unsupported languages,
  - exits 0 (fail-open) when the sidecar is unreachable, unless
    `S2_FAIL_CLOSED=1` is set in which case it exits 2.

Implementation rules:
  - Zero third-party deps. Uses stdlib `urllib.request` so the hook keeps
    working even if the project venv is broken (a hook crash blocks edits).
  - Robust to malformed stdin / unknown tool_input shapes — never block on
    a payload we can't parse.
  - Timeout configurable via S2_TIMEOUT (default 30s), matching the bridge.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SIDECAR_URL = os.getenv("S2_URL", "http://127.0.0.1:8765")
SCORE_ENDPOINT = f"{SIDECAR_URL}/score"


def _timeout_seconds() -> int:
    try:
        return int(os.getenv("S2_TIMEOUT", "30"))
    except ValueError:
        return 30


def _fail_closed() -> bool:
    return os.getenv("S2_FAIL_CLOSED", "0") == "1"


def _read_payload() -> Optional[Dict[str, Any]]:
    """Read the PreToolUse JSON payload from stdin. None on parse failure."""
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _read_before_src(file_path: str) -> str:
    p = Path(file_path)
    try:
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _extract_file_path(tool_input: Dict[str, Any]) -> Optional[str]:
    fp = tool_input.get("file_path") or tool_input.get("path")
    if isinstance(fp, str) and fp.strip():
        return fp
    return None


def _extract_changes(
    tool_name: str, tool_input: Dict[str, Any]
) -> List[Tuple[str, str]]:
    """Return a list of (before_src, after_src) pairs to score.

    For Edit: one pair using new_string against the file contents on disk.
    For Write: one pair using `content` against on-disk contents (or "").
    For MultiEdit: a pair per edits[*].new_string, scored sequentially against
      the same before-state on disk (the sidecar is stateless; sequential
      compounding would require mutating the file which we must not do here).
    """
    file_path = _extract_file_path(tool_input)
    if not file_path:
        return []
    before = _read_before_src(file_path)

    if tool_name == "Write":
        after = tool_input.get("content")
        if not isinstance(after, str):
            return []
        return [(before, after)]

    if tool_name == "Edit":
        after = tool_input.get("new_string")
        if not isinstance(after, str):
            return []
        return [(before, after)]

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return []
        pairs: List[Tuple[str, str]] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            after = edit.get("new_string")
            if isinstance(after, str):
                pairs.append((before, after))
        return pairs

    return []


def _post_score(file_path: str, before_src: str, after_src: str) -> Dict[str, Any]:
    """Call POST /score. Returns the parsed report dict.

    On any network/transport failure raises SidecarUnavailable. On HTTP 415
    returns a degraded dict matching the bridge contract. On any other non-200
    raises SidecarUnavailable so the caller applies fail-open / fail-closed.
    """
    body = json.dumps(
        {"path": file_path, "before_src": before_src, "after_src": after_src}
    ).encode("utf-8")
    req = urllib.request.Request(
        SCORE_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
            data = resp.read()
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise SidecarUnavailable("invalid_sidecar_json") from exc
            if not isinstance(parsed, dict):
                raise SidecarUnavailable("invalid_sidecar_json")
            return parsed
    except urllib.error.HTTPError as exc:
        if exc.code == 415:
            ext = Path(file_path).suffix
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
                if isinstance(err_body, dict) and isinstance(
                    err_body.get("extension"), str
                ):
                    ext = err_body["extension"]
            except Exception:  # noqa: BLE001
                pass
            return {
                "regression_detected": False,
                "degraded": True,
                "reason": "unsupported_language",
                "extension": ext,
            }
        raise SidecarUnavailable(f"http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SidecarUnavailable(str(exc)) from exc


class SidecarUnavailable(Exception):
    """Raised when the sidecar cannot be reached or returned an error status."""


def _format_block(file_path: str, report: Dict[str, Any]) -> str:
    ais = report.get("architectural_impact_score")
    coh = report.get("coherence_delta")
    summary = report.get("human_summary") or "(no summary)"
    risk_vector = report.get("risk_vector") or []
    risk_labels = report.get("risk_labels") or []
    dominant = "n/a"
    try:
        if (
            isinstance(risk_vector, list)
            and isinstance(risk_labels, list)
            and len(risk_vector) == len(risk_labels)
            and risk_vector
        ):
            idx = max(range(len(risk_vector)), key=lambda i: risk_vector[i])
            dominant = f"{risk_labels[idx]}={float(risk_vector[idx]):.2f}"
    except (TypeError, ValueError):
        dominant = "n/a"

    def _fmt(v: Any) -> str:
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "n/a"

    return (
        "[hybrid-reasoner] BLOCKED: architectural regression detected\n"
        f"  file: {file_path}\n"
        f"  AIS: {_fmt(ais)}  (threshold 0.40)\n"
        f"  coherence_delta: {_fmt(coh)}  (threshold 1.50)\n"
        f"  dominant_risk: {dominant}\n"
        f"  summary: {summary}\n"
    )


def _exit(code: int, stderr_msg: str = "") -> None:
    if stderr_msg:
        sys.stderr.write(stderr_msg)
        if not stderr_msg.endswith("\n"):
            sys.stderr.write("\n")
    sys.exit(code)


def main() -> None:
    payload = _read_payload()
    if payload is None:
        # Malformed stdin — never block the user on a bad payload.
        _exit(0)

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        _exit(0)
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        _exit(0)

    file_path = _extract_file_path(tool_input)
    if not file_path:
        _exit(0)

    pairs = _extract_changes(tool_name, tool_input)
    if not pairs:
        _exit(0)

    for before_src, after_src in pairs:
        try:
            report = _post_score(file_path, before_src, after_src)
        except SidecarUnavailable as exc:
            if _fail_closed():
                _exit(
                    2,
                    f"[hybrid-reasoner] BLOCKED: sidecar unavailable ({exc}); "
                    "S2_FAIL_CLOSED=1 in effect.",
                )
            _exit(
                0,
                f"[hybrid-reasoner] sidecar unavailable ({exc}); fail-open.",
            )
            return  # pragma: no cover - _exit raises

        if report.get("degraded") and report.get("reason") == "unsupported_language":
            ext = report.get("extension", Path(file_path).suffix)
            _exit(
                0,
                f"[hybrid-reasoner] skipped: unsupported_language ({ext}).",
            )
            return  # pragma: no cover

        if report.get("regression_detected") is True:
            _exit(2, _format_block(file_path, report))
            return  # pragma: no cover

    # All edits cleared.
    _exit(0)


if __name__ == "__main__":
    main()
