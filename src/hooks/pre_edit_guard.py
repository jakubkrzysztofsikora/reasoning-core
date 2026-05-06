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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Hooks dir on sys.path for shared audit_log import.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402
import _guard_paths  # type: ignore  # noqa: E402
from _block_format import format_block as _format_block  # type: ignore  # noqa: E402

# Centralized shadow-mode helper. Falls back to None on import error so the
# hook keeps working in degraded environments.
try:
    import _shadow_mode  # type: ignore
except ImportError:
    _shadow_mode = None  # type: ignore

try:
    import _calibration_gate  # type: ignore
except ImportError:
    _calibration_gate = None  # type: ignore

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

    For Edit: reconstructs the post-edit file by applying old_string→new_string
      to the disk content, so the SSM scores actual before/after rather than
      (whole_file, new_string) which would inflate churn to ~1.0 on every edit.
    For Write: one pair using `content` against on-disk contents (or "").
    For MultiEdit: applies each edit sequentially in-memory, scoring the final
      reconstructed content against the original disk state.
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
        new_str = tool_input.get("new_string")
        old_str = tool_input.get("old_string", "")
        if not isinstance(new_str, str) or not isinstance(old_str, str):
            return []
        if old_str and old_str in before:
            after = before.replace(old_str, new_str, 1)
        else:
            after = before
        return [(before, after)]

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return []
        working = before
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old_s = edit.get("old_string", "")
            new_s = edit.get("new_string", "")
            if isinstance(old_s, str) and isinstance(new_s, str) and old_s and old_s in working:
                working = working.replace(old_s, new_s, 1)
        return [(before, working)]

    return []


def _post_score(file_path: str, before_src: str, after_src: str) -> Dict[str, Any]:
    """Call POST /score. Returns the parsed report dict.

    On any network/transport failure raises SidecarUnavailable. On HTTP 415
    returns a degraded dict matching the bridge contract. On any other non-200
    raises SidecarUnavailable so the caller applies fail-open / fail-closed.
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    payload: Dict[str, Any] = {
        "path": file_path,
        "before_src": before_src,
        "after_src": after_src,
    }
    if sid:
        payload["session_id"] = sid
    body = json.dumps(payload).encode("utf-8")
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


def _exit(code: int, stderr_msg: str = "") -> None:
    if stderr_msg:
        sys.stderr.write(stderr_msg)
        if not stderr_msg.endswith("\n"):
            sys.stderr.write("\n")
    sys.exit(code)


def _emit_audit(
    *,
    tool_name: str,
    decision: str,
    file_path: Optional[str],
    started: float,
    before_src: str = "",
    after_src: str = "",
    report: Optional[Dict[str, Any]] = None,
    reason: str = "",
    retry_after_block: bool = False,
    signal_source: str = "",
) -> None:
    """Best-effort audit emit. Never raises."""
    try:
        latency_ms = int((time.time() - started) * 1000)
        ais = None
        coh = None
        regression = None
        risk_vector: List[float] = []
        cumulative_drift = None
        language = ""
        human_summary = ""
        if isinstance(report, dict):
            ais = report.get("architectural_impact_score")
            coh = report.get("coherence_delta")
            regression = report.get("regression_detected")
            risk_vector = report.get("risk_vector") or []
            cumulative_drift = report.get("cumulative_drift")
            human_summary = report.get("human_summary") or ""
        ext = Path(file_path or "").suffix.lstrip(".") or ""
        audit_log.append_event(audit_log.new_event(
            tool_name=tool_name,
            decision=decision,
            file_path=file_path,
            language=ext,
            ais=ais,
            coherence_delta=coh,
            regression_detected=regression,
            risk_vector=list(risk_vector) if isinstance(risk_vector, (list, tuple)) else [],
            cumulative_drift=cumulative_drift,
            latency_ms=latency_ms,
            before_bytes=len((before_src or "").encode("utf-8", errors="replace")),
            after_bytes=len((after_src or "").encode("utf-8", errors="replace")),
            retry_after_block=retry_after_block,
            reason=reason or human_summary,
            signal_source=signal_source or (
                "mock_heuristic" if (isinstance(report, dict) and report.get("mock_detector_triggered")) else "ssm"
            ),
        ))
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    started = time.time()
    payload = _read_payload()
    if payload is None:
        # Malformed stdin — never block the user on a bad payload.
        _emit_audit(
            tool_name="Edit",
            decision="allowed",
            file_path=None,
            started=started,
            reason="malformed_payload",
        )
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

    # Layer 1: lock guard-files FIRST (before any override path). Without this
    # ordering, kill_switches.bypass_next or a `# rc:skip` magic comment becomes
    # a guard-bypass primitive — an agent could rewrite the hooks themselves.
    # Edits to the hook scripts, settings.json, sidecar source, or grammar
    # loader are denied unless the operator explicitly set RC_ALLOW_GUARD_EDIT=1
    # in the shell that started Claude. Discovery + matching live in
    # `_guard_paths` so a new hook script is auto-locked at import (Phase 0).
    is_retry = audit_log.is_retry_after_block(file_path)
    if _guard_paths.is_guarded(file_path) and not _guard_paths.is_override_active():
        audit_log.record_block(file_path)
        _emit_audit(
            tool_name=tool_name,
            decision="blocked",
            file_path=file_path,
            started=started,
            reason="guard_file_locked",
            retry_after_block=is_retry,
        )
        _exit(
            2,
            "[hybrid-reasoner] BLOCKED: guard-file edits denied.\n"
            f"  file: {file_path}",
        )
        return  # pragma: no cover

    # Layer 2: day-zero override check. Magic comments and kill switches let
    # the operator bypass scoring without restarting Claude. Read at hook call
    # time. Allow path emits decision=allowed_via_override so the override is
    # observable. Hardening:
    #   - Magic comments are honored ONLY from the on-disk file (operator
    #     authored). Agent-introduced directives via Write/new_string content
    #     do not bypass scoring.
    #   - If the directive is being newly introduced by THIS edit (not present
    #     before but present after), tag self_introduced and decline override.
    try:
        import _kill_switches  # type: ignore
        import _magic_comments  # type: ignore
    except ImportError:
        _kill_switches = None  # type: ignore
        _magic_comments = None  # type: ignore
    if _kill_switches is not None:
        if _kill_switches.is_disabled_globally() or _kill_switches.consume_bypass_next() or _kill_switches.is_file_skipped(file_path):
            _emit_audit(
                tool_name=tool_name,
                decision="allowed_via_override",
                file_path=file_path,
                started=started,
                reason="kill_switch_or_bypass_next",
            )
            _exit(0)
    if _magic_comments is not None:
        before_for_directive = _read_before_src(file_path)  # operator-authored only
        directive_before = _magic_comments.parse(before_for_directive)
        # Build candidate after_src to detect agent self-introduction
        after_for_directive = before_for_directive
        if tool_name == "Write":
            after_for_directive = tool_input.get("content") or ""
        elif tool_name == "Edit":
            old_s = tool_input.get("old_string", "") or ""
            new_s = tool_input.get("new_string", "") or ""
            if old_s and old_s in before_for_directive:
                after_for_directive = before_for_directive.replace(old_s, new_s, 1)
        directive_after = _magic_comments.parse(after_for_directive)
        self_introduced = (
            directive_before is None and directive_after is not None
        ) or (
            directive_before is not None
            and directive_after is not None
            and directive_before.name != directive_after.name
        )
        if self_introduced and directive_after is not None:
            _emit_audit(
                tool_name=tool_name,
                decision="override_declined",
                file_path=file_path,
                started=started,
                reason=f"magic_comment_self_introduced:{directive_after.name}",
            )
            # Do not bypass; fall through to normal scoring path.
        elif _magic_comments.bypasses_all(directive_before):
            _emit_audit(
                tool_name=tool_name,
                decision="allowed_via_override",
                file_path=file_path,
                started=started,
                reason=f"magic_comment:{directive_before.name}:{directive_before.reason}",
            )
            _exit(0)

    # Layer 3: Language fingerprint lock (P3 Invariant 1). Deny edits whose
    # extension doesn't match the session manifest's declared language family
    # — agent abandoning .NET for Python in a long session no longer slips
    # past as quality-flat. Operator override: # rc:skip-lang magic comment
    # OR RC_LANG_ALLOW=py,sh OR RC_LANG_OVERRIDE=1.
    if (
        os.environ.get("RC_LANG_LOCK") == "1"
        and os.environ.get("RC_LANG_OVERRIDE") != "1"
    ):
        try:
            import _session_manifest  # type: ignore
            cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
            task_spec = os.environ.get("RC_TASK_SPEC") or ""
            key = _session_manifest.manifest_key(cwd, task_spec)
            mani = _session_manifest.load(key)
            lang_skip = (
                _magic_comments is not None
                and _magic_comments.bypasses(
                    _magic_comments.parse(_read_before_src(file_path)),
                    "lang",
                )
            )
            if mani and not lang_skip and not _session_manifest.is_path_allowed(mani, file_path):
                shadow = _shadow_mode.is_active() if _shadow_mode else False
                decision = "shadow_blocked" if shadow else "blocked"
                _emit_audit(
                    tool_name=tool_name,
                    decision=decision,
                    file_path=file_path,
                    started=started,
                    reason="language_fingerprint_violation",
                    signal_source="lang_lock",
                )
                if shadow:
                    audit_log.record_shadow_block(file_path)
                else:
                    audit_log.record_block(file_path)
                    declared = mani.get("declared_language")
                    _exit(
                        2,
                        f"[hybrid-reasoner] BLOCKED: language fingerprint violation\n"
                        f"  file: {file_path}\n"
                        f"  declared: {declared}",
                    )
        except Exception:  # noqa: BLE001
            pass

    pairs = _extract_changes(tool_name, tool_input)
    if not pairs:
        _exit(0)

    # Track whether ANY pair tripped a shadow_blocked decision so we don't
    # silently emit a final `allowed` row that double-counts a shadow event.
    shadow_hit = False
    for before_src, after_src in pairs:
        try:
            report = _post_score(file_path, before_src, after_src)
        except SidecarUnavailable as exc:
            # SHADOW=1 honored at sidecar-unavailable too. Calibration window
            # must not produce hard-blocks on infra flake. (Reviewer #7.)
            if _fail_closed() and not (_shadow_mode.is_active() if _shadow_mode else False):
                _emit_audit(
                    tool_name=tool_name,
                    decision="blocked",
                    file_path=file_path,
                    started=started,
                    before_src=before_src,
                    after_src=after_src,
                    reason=f"sidecar_unavailable_fail_closed:{exc}",
                    retry_after_block=is_retry,
                )
                audit_log.record_block(file_path)
                _exit(
                    2,
                    f"[hybrid-reasoner] BLOCKED: sidecar unavailable ({exc}); "
                    "S2_FAIL_CLOSED=1 in effect.",
                )
            _emit_audit(
                tool_name=tool_name,
                decision="fail-open",
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                reason=f"sidecar_unavailable:{exc}",
                retry_after_block=is_retry,
            )
            _exit(
                0,
                f"[hybrid-reasoner] sidecar unavailable ({exc}); fail-open.",
            )
            return  # pragma: no cover - _exit raises

        if report.get("degraded") and report.get("reason") == "unsupported_language":
            ext = report.get("extension", Path(file_path).suffix)
            _emit_audit(
                tool_name=tool_name,
                decision="unsupported",
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=f"unsupported_language:{ext}",
                retry_after_block=is_retry,
            )
            _exit(
                0,
                f"[hybrid-reasoner] skipped: unsupported_language ({ext}).",
            )
            return  # pragma: no cover

        # P1 mock-detector: if the SSM didn't flag regression but this is
        # a test_code path AND the heuristic mock-detector reports likely
        # mock-instead-of-integrate, raise a flag (heuristic, not SSM).
        # Honored under RC_MOCK_DETECTOR=1. Operator can bypass per-file
        # via # rc:skip-mock magic comment. Audit row carries
        # signal_source=mock_heuristic so FPR analysis can separate it
        # from SSM-driven blocks.
        try:
            mock_skip = False
            if _magic_comments is not None:
                # before_for_directive captured earlier in Layer-2 logic
                pre_directive = _magic_comments.parse(_read_before_src(file_path))
                if _magic_comments.bypasses(pre_directive, "mock"):
                    mock_skip = True
            if (
                not mock_skip
                and os.environ.get("RC_MOCK_DETECTOR") == "1"
                and isinstance(report, dict)
                and report.get("file_kind") == "test_code"
                and report.get("regression_detected") is not True
            ):
                import _mock_detector  # type: ignore
                from pathlib import Path as _Path
                project_root = _Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
                if _mock_detector.is_likely_mocked(after_src, project_root):
                    auth = _mock_detector.integration_authenticity(after_src, project_root)
                    report = dict(report)
                    report["regression_detected"] = True
                    report["mock_detector_triggered"] = True
                    report["signal_source"] = "mock_heuristic"
                    report["integration_authenticity"] = auth
                    report["human_summary"] = (
                        report.get("human_summary", "")
                        + f" | mock-detector flagged: integration_authenticity={auth:.2f}"
                    )
        except Exception:  # noqa: BLE001
            pass

        # P3 Invariant 2: cumulative_drift gate. Sidecar emits the value but
        # nothing previously gated on it.
        #
        # PLACEHOLDER THRESHOLDS: 4.0 (warn) / 6.0 (deny) are reviewer-tuned,
        # NOT data-driven. P7 recalibrates from synthetic CUSUM injections at
        # 5% type-I error rate. Until then operators flipping RC_SHADOW_MODE=0
        # block at thresholds whose statistical meaning is undefined.
        # See deferred tracker #19 + #21.
        # Override: RC_DRIFT_OVERRIDE=1 (hard-denied in pre_bash_guard so
        # agent cannot self-arm via Bash command-line prefix).
        if (
            isinstance(report, dict)
            and os.environ.get("RC_DRIFT_OVERRIDE") != "1"
        ):
            try:
                drift = report.get("cumulative_drift")
                if isinstance(drift, (int, float)):
                    drift_warn = float(os.environ.get("RC_DRIFT_WARN", "4.0"))
                    drift_deny = float(os.environ.get("RC_DRIFT_DENY", "6.0"))
                    if drift > drift_deny and not isinstance(report, dict):
                        pass  # unreachable; type guard
                    if drift > drift_deny:
                        if _shadow_mode.is_active() if _shadow_mode else False:
                            shadow_hit = True
                            audit_log.record_shadow_block(file_path)
                            _emit_audit(
                                tool_name=tool_name,
                                decision="shadow_blocked",
                                file_path=file_path,
                                started=started,
                                before_src=before_src,
                                after_src=after_src,
                                report=report,
                                reason=f"cumulative_drift_exceeds:{drift:.2f}>{drift_deny}",
                                signal_source="drift_gate",
                            )
                            continue
                        audit_log.record_block(file_path)
                        _emit_audit(
                            tool_name=tool_name,
                            decision="blocked",
                            file_path=file_path,
                            started=started,
                            before_src=before_src,
                            after_src=after_src,
                            report=report,
                            reason=f"cumulative_drift_exceeds:{drift:.2f}>{drift_deny}",
                            signal_source="drift_gate",
                        )
                        _exit(
                            2,
                            f"[hybrid-reasoner] BLOCKED: cumulative_drift {drift:.2f} "
                            f"exceeds threshold {drift_deny:.2f}",
                        )
                        return  # pragma: no cover
                    elif drift > drift_warn:
                        sys.stderr.write(
                            f"[hybrid-reasoner] WARN: cumulative_drift {drift:.2f} "
                            f"exceeds warn threshold {drift_warn:.2f}\n"
                        )
            except (TypeError, ValueError):
                pass

        # P7: Mahalanobis calibration gate (RC_CALIBRATION_ENABLED=1).
        # Default OFF — wired DARK until P4 benign corpus exists. When on,
        # scores the risk_vector through a per-kind CalibrationModel and
        # logs the result alongside the existing per-dim signals so
        # operators can compare both rules during the shadow window.
        calibration_result: Optional[Dict[str, Any]] = None
        if (
            _calibration_gate is not None
            and isinstance(report, dict)
            and _calibration_gate.is_enabled()
        ):
            try:
                rv = report.get("risk_vector") or []
                fk = report.get("file_kind")
                calibration_result = _calibration_gate.evaluate(rv, file_kind=fk)
            except Exception:  # noqa: BLE001
                calibration_result = None
            if calibration_result is not None:
                # Surface on the report so audit row + downstream consumers
                # see calibration score next to per-dim scores.
                report = dict(report)
                report["calibration"] = calibration_result
                if calibration_result.get("anomaly") and not report.get("regression_detected"):
                    # Calibration disagrees with the per-dim rule. Treat as
                    # advisory anomaly — enforce per existing shadow posture.
                    if _shadow_mode is not None and _shadow_mode.is_active():
                        shadow_hit = True
                        audit_log.record_shadow_block(file_path)
                        _emit_audit(
                            tool_name=tool_name,
                            decision="shadow_blocked",
                            file_path=file_path,
                            started=started,
                            before_src=before_src,
                            after_src=after_src,
                            report=report,
                            reason=(
                                f"calibration_anomaly:score={calibration_result['score']:.2f}"
                                f">thr={calibration_result['threshold']:.2f}"
                                f":kind={calibration_result['kind_used']}"
                            ),
                            signal_source="calibration",
                        )
                        continue
                    # Shadow OFF: log advisory but do NOT block (P7 dark
                    # rollout — calibration is in a comparison window, not
                    # an enforcement role yet).
                    sys.stderr.write(
                        f"[hybrid-reasoner] calibration anomaly "
                        f"(score={calibration_result['score']:.2f} > "
                        f"thr={calibration_result['threshold']:.2f}, "
                        f"kind={calibration_result['kind_used']}) — advisory only\n"
                    )

        if report.get("regression_detected") is True:
            # RC_SHADOW_MODE=1: log decision but DO NOT enforce. Used during
            # P4 calibration window so shadow-FPR can be measured before
            # promoting any P1-P3 invariant to enforcement.
            if _shadow_mode.is_active() if _shadow_mode else False:
                shadow_hit = True
                # Record into shadow-marker namespace, NOT the main retry
                # marker. Otherwise legit operator retries after a shadow
                # block get tagged retry_after_block=True misclassifying
                # audit data. Reviewer-flagged shadow retry-misfire fix.
                audit_log.record_shadow_block(file_path)
                _emit_audit(
                    tool_name=tool_name,
                    decision="shadow_blocked",
                    file_path=file_path,
                    started=started,
                    before_src=before_src,
                    after_src=after_src,
                    report=report,
                    reason="regression_detected_shadow",
                    retry_after_block=is_retry,
                )
                continue  # log only; do not enforce
            audit_log.record_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision="blocked",
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason="regression_detected",
                retry_after_block=is_retry,
            )
            _exit(2, _format_block(file_path, report, is_retry=is_retry))
            return  # pragma: no cover

    # All edits cleared.
    # If any pair landed shadow_blocked, the shadow row has already been
    # emitted — do NOT also emit `allowed` for the same edit (double-count).
    if shadow_hit:
        _exit(0)
    _emit_audit(
        tool_name=tool_name,
        decision="allowed",
        file_path=file_path,
        started=started,
        before_src=pairs[-1][0] if pairs else "",
        after_src=pairs[-1][1] if pairs else "",
        report=report if 'report' in locals() else None,
        reason="ok",
        retry_after_block=is_retry,
    )
    _exit(0)


if __name__ == "__main__":
    main()
