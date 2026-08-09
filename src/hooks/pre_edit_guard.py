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
import socket
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
import _dispatch  # type: ignore  # noqa: E402
from _block_format import format_block as _format_block  # type: ignore  # noqa: E402

# Phase 2 execution-grounded oracles and cumulative patch tracker.
try:
    import _oracles  # type: ignore
except ImportError:
    _oracles = None  # type: ignore

try:
    import _patch_tracker  # type: ignore
except ImportError:
    _patch_tracker = None  # type: ignore

# Centralized shadow-mode helper (activated by RC_SHADOW_MODE=1). Falls back
# to None on import error so the hook keeps working in degraded environments.
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


def _hard_cap_seconds() -> float:
    """Client-side hard cap on the /score POST.

    Audit 2026-06-01 §1.4: sidecar p95=58s, p99=60s. Without a cap the
    agent can stall for a full minute per Edit. Defaults to 1500ms; never
    exceeds S2_TIMEOUT (the upstream HTTP read timeout). On cap-exceeded
    the caller falls back to the symbolic gate (rule_engine + lang_lock)
    and audits reason="symbolic_fallback".
    """
    try:
        cap_ms = int(os.getenv("S2_HARD_CAP_MS", "1500"))
    except ValueError:
        cap_ms = 1500
    return cap_ms / 1000.0


def _effective_score_timeout() -> float:
    return min(_hard_cap_seconds(), float(_timeout_seconds()))


def _fail_closed() -> bool:
    return os.getenv("S2_FAIL_CLOSED", "0") == "1"


def _project_dir() -> str:
    """Resolve the project directory the gate is watching."""
    return str(
        Path(
            os.environ.get("RC_RUN_DIR")
            or os.environ.get("RC_PROJECT_DIR")
            or os.getcwd()
        ).resolve()
    )


def _read_payload() -> Optional[Dict[str, Any]]:
    """Read the PreToolUse JSON payload from stdin via the adapter layer.

    Routed through ``adapters.claude.parse_stdin`` (Phase 1a) so the same
    dispatch chain serves Gemini / Copilot / Vibe — but for back-compat
    this thin wrapper still returns the raw payload dict (the rest of
    ``main()`` reads ``payload["tool_name"]`` etc.). Returns ``None`` on
    parse failure to preserve the malformed-payload audit branch.

    Phase 1a review: emit a stderr canary if the adapter import fails so
    the silent-fallback path isn't invisible. ``RC_ADAPTER_REQUIRED=1``
    hard-fails (CI canary).
    """
    try:
        from src.hooks.adapters import claude as _claude_adapter  # type: ignore
        env = _claude_adapter.parse_stdin("PreToolUse")
        if env.tool_name is None and not env.raw:
            return None
        return dict(env.raw)
    except Exception as exc:  # noqa: BLE001 - fall through to legacy path
        try:
            sys.stderr.write(
                f"[hybrid-reasoner] adapter fallback (PreToolUse): {exc!r}\n"
            )
        except Exception:  # noqa: BLE001
            pass
        if os.environ.get("RC_ADAPTER_REQUIRED") == "1":
            raise
    # Legacy path retained for environments where the adapter package
    # cannot be imported (e.g. partial installs in CI sandboxes).
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
    # Audit 2026-06-01 §Summary #1: without session_id the sidecar can never
    # compute Phase-2 dims (session_centroid_drift, project_fan_in,
    # project_coupling). CLAUDE_SESSION_ID is rarely set in real hook runs;
    # fall back to the stable per-process id audit_log already uses.
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        try:
            import audit_log as _al  # type: ignore
            sid = _al._session_id()
        except Exception:  # noqa: BLE001
            sid = None
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
    cap_s = _effective_score_timeout()
    try:
        with urllib.request.urlopen(req, timeout=cap_s) as resp:
            data = resp.read()
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise SidecarUnavailable("invalid_sidecar_json") from exc
            if not isinstance(parsed, dict):
                raise SidecarUnavailable("invalid_sidecar_json")
            return parsed
    except (socket.timeout, TimeoutError) as exc:
        cap_ms = int(cap_s * 1000)
        try:
            sys.stderr.write(
                f"[hybrid-reasoner] sidecar hard cap exceeded ({cap_ms}ms); "
                f"symbolic fallback engaged.\n"
            )
        except Exception:  # noqa: BLE001
            pass
        raise SidecarUnavailable(f"hard_cap_exceeded:{cap_ms}ms") from exc
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
    if code == 2:
        last = audit_log.last_event()
        decision_id = last.get("decision_id") if isinstance(last, dict) else None
        if decision_id:
            sys.stderr.write(
                f"\n[hybrid-reasoner] Decision ID: {decision_id}\n"
                f"  Inspect: rc explain {decision_id}\n"
                f"  Override: rc bypass-next\n"
            )
    sys.exit(code)


_AUDIT_RESERVED_KEYS = frozenset({
    "tool_name", "decision", "file_path", "language",
    "ais", "coherence_delta", "regression_detected", "risk_vector",
    "cumulative_drift", "latency_ms", "before_bytes", "after_bytes",
    "retry_after_block", "reason", "signal_source", "gate_id",
    # Set by audit_log.new_event itself:
    "ts", "decision_id", "session_id", "project_dir",
})


# Audit 2026-06-01 infra-1: map signal_source → gate_id so per-gate
# ablation queries on the audit log work without hand-mapping. The audit_log
# module already declares GATE_IDS = {scorer, plan_grounding, rules,
# calibration, lang_lock, mock_detector, drift_gate}.
_SIGNAL_SOURCE_TO_GATE_ID: Dict[str, str] = {
    "ssm": "scorer",
    "mock_heuristic": "mock_detector",
    "plan_grounding": "plan_grounding",
    "lang_lock": "lang_lock",
    "drift_gate": "drift_gate",
    "calibration": "calibration",
    "rules": "rules",
    "rule_engine": "rules",
    "prm": "plan_grounding",
    "symbolic_fallback": "rules",
}


def _rc_mode() -> str:
    """Return the active reasoning-core mode.

    - advise   : warn only, never block on contract/oracle/rule failures.
                 Guard-file self-protection still blocks.
    - copilot  : block on contract, oracle, and rule failures.
    - autopilot: block and auto-repair within policy (Phase 0: same
                 enforcement posture as copilot; auto-repair scaffolding
                 lands in Phase 2).
    """
    mode = os.environ.get("RC_MODE", "advise").strip().lower()
    if mode in ("copilot", "autopilot"):
        return mode
    return "advise"


def _apply_mode(outcome: _dispatch.GateOutcome) -> _dispatch.GateOutcome:
    """Downgrade enforcement outcomes to advisory when RC_MODE=advise."""
    if _rc_mode() != "advise":
        return outcome
    if outcome.action == "exit_block":
        return _dispatch.GateOutcome(
            action="stderr_only",
            code=0,
            stderr=outcome.stderr,
            decision="warn",
            reason=outcome.reason,
            signal_source=outcome.signal_source,
            report=outcome.report,
            audit_extra=outcome.audit_extra,
        )
    if outcome.action == "continue_pair":
        return _dispatch.GateOutcome(
            action="audit_only",
            code=0,
            stderr="",
            decision="shadow_advisory",
            reason=outcome.reason,
            signal_source=outcome.signal_source,
            report=outcome.report,
            audit_extra=outcome.audit_extra,
        )
    return outcome


def _symbolic_fallback(
    file_path: str,
    before_src: str,
    after_src: str,
    read_before_src,
) -> _dispatch.GateOutcome:
    """Run symbolic gates when the SSM sidecar exceeds S2_HARD_CAP_MS.

    Returns the most severe outcome found, tagged with
    signal_source="symbolic_fallback". A clean result is returned as
    action="pass" so the orchestrator can emit a fallback audit event.
    """
    outcomes: List[_dispatch.GateOutcome] = []
    outcomes.append(
        _dispatch.gate_rule_engine(
            file_path=file_path,
            before_src=before_src,
            after_src=after_src,
            report={},
        )
    )
    outcomes.append(
        _dispatch.gate_lang_lock(file_path=file_path, read_before_src=read_before_src)
    )
    outcomes.append(_dispatch.gate_plan_grounding(file_path=file_path))

    for action in ("exit_block", "continue_pair", "stderr_only", "audit_only"):
        for o in outcomes:
            if o.action == action:
                return _dispatch.GateOutcome(
                    action=o.action,
                    code=o.code,
                    stderr=o.stderr,
                    decision=o.decision,
                    reason=o.reason,
                    signal_source="symbolic_fallback",
                    report=o.report,
                    audit_extra=o.audit_extra,
                )
    return _dispatch.GateOutcome(action="pass", signal_source="symbolic_fallback")


def _handle_prm_outcome(
    *,
    tool_name: str,
    file_path: str,
    started: float,
    before_src: str,
    after_src: str,
    is_retry: bool,
) -> bool:
    """Run the PRM gate, record shadow events, and honor block/warn outcomes.

    Returns True if the outer pair loop should continue (no block), False if
    the function already emitted the audit and exited/warned.
    """
    try:
        plan_path = _dispatch._resolve_plan_path()
        plan_text = plan_path.read_text(encoding="utf-8", errors="replace") if plan_path else None
    except Exception:  # noqa: BLE001
        plan_text = None
    outcome = _dispatch.gate_prm(
        file_path=file_path,
        before_src=before_src,
        after_src=after_src,
        plan_text=plan_text,
    )
    if outcome.action == "pass" and not outcome.signal_source:
        return True

    # Record every scored PRM event for promotion tracking.
    if outcome.audit_extra and "prm_score" in outcome.audit_extra:
        try:
            from _prm_promotion import record_shadow_event  # type: ignore

            record_shadow_event(
                project_root=_project_dir(),
                score=outcome.audit_extra["prm_score"],
            )
        except Exception:  # noqa: BLE001
            pass

    # Apply RC_MODE downgrade to the PRM outcome.
    outcome = _apply_mode(outcome)

    if outcome.action == "stderr_only":
        sys.stderr.write(outcome.stderr)
        _emit_audit(
            tool_name=tool_name,
            decision=outcome.decision,
            file_path=file_path,
            started=started,
            before_src=before_src,
            after_src=after_src,
            reason=outcome.reason,
            signal_source=outcome.signal_source,
            retry_after_block=is_retry,
            extra={**(outcome.audit_extra or {}), "rc_mode": _rc_mode()},
        )
        return True
    if outcome.action == "exit_block":
        audit_log.record_block(file_path)
        _emit_audit(
            tool_name=tool_name,
            decision=outcome.decision,
            file_path=file_path,
            started=started,
            before_src=before_src,
            after_src=after_src,
            reason=outcome.reason,
            signal_source=outcome.signal_source,
            retry_after_block=is_retry,
            extra={**(outcome.audit_extra or {}), "rc_mode": _rc_mode()},
        )
        _exit(outcome.code, outcome.stderr)
    # audit_only / continue_pair / pass
    _emit_audit(
        tool_name=tool_name,
        decision=outcome.decision,
        file_path=file_path,
        started=started,
        before_src=before_src,
        after_src=after_src,
        reason=outcome.reason,
        signal_source=outcome.signal_source,
        retry_after_block=is_retry,
        extra={**(outcome.audit_extra or {}), "rc_mode": _rc_mode()},
    )
    return True


# Keep the bare _emit_audit name for internal compatibility; new code should
# pass rc_mode via extra so every audit row carries the active mode.
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
    gate_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort audit emit. Never raises.

    ``extra`` (iter-3): per-gate audit_extra dict. Keys are folded into the
    event as siblings of the standard fields so downstream aggregators can
    consume gate-specific signals (e.g. plan_grounding's plan_refs_count).

    Returns the emitted event (with decision_id), or None on failure.
    """
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
        effective_signal = signal_source or (
            "mock_heuristic" if (isinstance(report, dict) and report.get("mock_detector_triggered")) else "ssm"
        )
        # Derive gate_id from signal_source if caller didn't pin one explicitly.
        effective_gate_id = gate_id or _SIGNAL_SOURCE_TO_GATE_ID.get(effective_signal)
        event = audit_log.new_event(
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
            signal_source=effective_signal,
            gate_id=effective_gate_id,
            # Filter reserved keys so a future gate stuffing `reason`/`signal_source`
            # into audit_extra doesn't crash the splat into TypeError (which would
            # then be silently swallowed by the bare except below — losing the audit
            # row entirely). Engineer-flagged in iter-3 phase-validation review.
            **{k: v for k, v in (extra or {}).items() if k not in _AUDIT_RESERVED_KEYS},
        )
        return audit_log.append_event(event)
    except Exception:  # noqa: BLE001
        return None


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
                extra={"git_head": audit_log._get_git_head()},
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
                extra={"git_head": audit_log._get_git_head()},
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
            try:
                from src.hooks import _host_env  # type: ignore
                cwd = str(_host_env.project_dir())
            except Exception:  # noqa: BLE001
                cwd = os.environ.get("RC_PROJECT_DIR") or os.getcwd()
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
                # RC_MODE=advise downgrades the lang-lock block to a warning.
                if not shadow and _rc_mode() == "advise":
                    declared = mani.get("declared_language")
                    file_lang = _session_manifest.language_for_path(file_path)
                    _emit_audit(
                        tool_name=tool_name,
                        decision="warn",
                        file_path=file_path,
                        started=started,
                        reason="language_fingerprint_violation",
                        signal_source="lang_lock",
                        extra={"rc_mode": "advise"},
                    )
                    sys.stderr.write(
                        f"[hybrid-reasoner] WARN: language fingerprint violation\n"
                        f"  file: {file_path}\n"
                        f"  declared: {declared}\n"
                        f"  file_lang: {file_lang}\n"
                    )
                else:
                    decision = "shadow_blocked" if shadow else "blocked"
                    _emit_audit(
                        tool_name=tool_name,
                        decision=decision,
                        file_path=file_path,
                        started=started,
                        reason="language_fingerprint_violation",
                        signal_source="lang_lock",
                        extra={"rc_mode": _rc_mode()},
                    )
                    if shadow:
                        audit_log.record_shadow_block(file_path)
                    else:
                        audit_log.record_block(file_path)
                        declared = mani.get("declared_language")
                        # Round-2 P3 polyglot fix: list-format declared is fine
                        # for f-string display ("['csharp', 'javascript']"). Hint
                        # text helps operators self-unblock without bypassing.
                        file_lang = _session_manifest.language_for_path(file_path)
                        _exit(
                            2,
                            f"[hybrid-reasoner] BLOCKED: language fingerprint violation\n"
                            f"  file: {file_path}\n"
                            f"  declared: {declared}\n"
                            f"  file_lang: {file_lang}\n"
                            f"  hint: set RC_LANG_ALLOW=.{file_lang} OR add the file's\n"
                            f"        top-level dir to RC_LANG_LOCK_PATH_EXEMPT, then\n"
                            f"        delete the manifest at\n"
                            f"        ~/.local/state/reasoning-core/sessions/<key>.json\n"
                            f"        and start a new Claude Code session.",
                        )
        except Exception:  # noqa: BLE001
            pass

    # Iter-3 lever — plan-impl coupling (RC_PLAN_GROUNDING).
    # Default off. Mode 1 = warn (stderr advisory + audit), mode 2 = hard block.
    # Audit-only path keeps the signal invisible to the agent (no path-stuffing
    # incentive); see thoughts/shared/plans/2026-05-07-iter3-decisive-win.md §3.
    pg_outcome = _apply_mode(_dispatch.gate_plan_grounding(file_path=file_path))
    if pg_outcome.action == "stderr_only":
        sys.stderr.write(pg_outcome.stderr)
        _emit_audit(
            tool_name=tool_name,
            decision=pg_outcome.decision,
            file_path=file_path,
            started=started,
            reason=pg_outcome.reason,
            signal_source=pg_outcome.signal_source,
            extra={**(pg_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
        )
    elif pg_outcome.action == "exit_block":
        audit_log.record_block(file_path)
        _emit_audit(
            tool_name=tool_name,
            decision=pg_outcome.decision,
            file_path=file_path,
            started=started,
            reason=pg_outcome.reason,
            signal_source=pg_outcome.signal_source,
            extra={**(pg_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
        )
        _exit(pg_outcome.code, pg_outcome.stderr)
    elif pg_outcome.action == "audit_only":
        # B3 (sweep round-5): gate was active but couldn't run (no PLAN.md
        # or extractor unavailable). Emit audit event so aggregator can
        # detect silent evaporation, then continue the gate chain.
        _emit_audit(
            tool_name=tool_name,
            decision=pg_outcome.decision,
            file_path=file_path,
            started=started,
            reason=pg_outcome.reason,
            signal_source=pg_outcome.signal_source,
            extra={**(pg_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
        )

    pairs = _extract_changes(tool_name, tool_input)
    if not pairs:
        _exit(0)

    # Track whether ANY pair already produced an audit row (shadow block,
    # symbolic fallback decision, etc.) so we don't silently emit a final
    # `allowed` row that double-counts the event.
    pair_has_audit_row = False
    for before_src, after_src in pairs:
        # Phase 1: rich contract checks (imports/invariants) using the
        # proposed source. Path-level plan grounding was already evaluated
        # before the pair loop; this call only adds new violations.
        rich_pg = _apply_mode(
            _dispatch.gate_plan_grounding(
                file_path=file_path, after_src=after_src, path_check=False
            )
        )
        if rich_pg.action == "stderr_only":
            sys.stderr.write(rich_pg.stderr)
            pair_has_audit_row = True
            _emit_audit(
                tool_name=tool_name,
                decision=rich_pg.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                reason=rich_pg.reason,
                signal_source=rich_pg.signal_source,
                retry_after_block=is_retry,
                extra={**(rich_pg.audit_extra or {}), "rc_mode": _rc_mode()},
            )
        elif rich_pg.action == "exit_block":
            audit_log.record_block(file_path)
            pair_has_audit_row = True
            _emit_audit(
                tool_name=tool_name,
                decision=rich_pg.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                reason=rich_pg.reason,
                signal_source=rich_pg.signal_source,
                retry_after_block=is_retry,
                extra={**(rich_pg.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            _exit(rich_pg.code, rich_pg.stderr)
        elif rich_pg.action == "audit_only":
            pair_has_audit_row = True
            _emit_audit(
                tool_name=tool_name,
                decision=rich_pg.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                reason=rich_pg.reason,
                signal_source=rich_pg.signal_source,
                retry_after_block=is_retry,
                extra={**(rich_pg.audit_extra or {}), "rc_mode": _rc_mode()},
            )

        # Project structural checks are deterministic and run before neural
        # scoring.  Explicit allowlists are deliberately narrow: a matching
        # cycle or duplicate key must be named by the operator.
        try:
            from src import project_index as _project_index  # type: ignore
            root = str(_project_dir())
            cycle = _project_index.find_import_cycle(root, file_path, after_src)
            duplicates = _project_index.find_duplicate_definitions(root, file_path, after_src)
            cycle_key = " -> ".join(cycle) if cycle else ""
            cycle_allowed = cycle_key in {x.strip() for x in os.environ.get("RC_IMPORT_CYCLE_ALLOWLIST", "").split(",") if x.strip()}
            duplicate_allow = {x.strip() for x in os.environ.get("RC_DUPLICATE_ALLOWLIST", "").split(",") if x.strip()}
            unallowed_duplicates = [item for item in duplicates if f"{item['symbol']}@{item['path']}" not in duplicate_allow]
            structural_reason = ""
            if cycle and not cycle_allowed:
                structural_reason = f"import_cycle:{cycle_key}"
            elif unallowed_duplicates:
                first_duplicate = unallowed_duplicates[0]
                structural_reason = f"duplicate_definition:{first_duplicate['kind']}:{first_duplicate['symbol']}@{first_duplicate['path']}:{first_duplicate['line']}"
            if structural_reason:
                message = (
                    "[reasoning-core] BLOCKED: deterministic structural policy\n"
                    f"  reason: {structural_reason}\n"
                    "  hint: repair the import/definition, or add the exact entry to the operator allowlist.\n"
                )
                extra = {"import_cycle": cycle, "duplicate_definitions": duplicates, "rc_mode": _rc_mode()}
                if _rc_mode() != "advise" and os.environ.get("RC_STRUCTURAL_BLOCK", "1") == "1":
                    audit_log.record_block(file_path)
                    _emit_audit(tool_name=tool_name, decision="blocked", file_path=file_path, started=started,
                                before_src=before_src, after_src=after_src, reason=structural_reason,
                                signal_source="structural", retry_after_block=is_retry, extra=extra)
                    _exit(2, message)
                sys.stderr.write(message.replace("BLOCKED", "WARN"))
                _emit_audit(tool_name=tool_name, decision="warn", file_path=file_path, started=started,
                            before_src=before_src, after_src=after_src, reason=structural_reason,
                            signal_source="structural", retry_after_block=is_retry, extra=extra)
                pair_has_audit_row = True
        except Exception:  # noqa: BLE001 - unavailable index never becomes a hidden block
            pass

        # Phase 2: execution-grounded oracles. Track the cumulative patch and
        # run fast T1/T2 checks before the expensive sidecar call.
        if _patch_tracker is not None:
            try:
                _patch_tracker.append_edit(
                    project_root=_project_dir(),
                    file_path=file_path,
                    before_src=before_src,
                    after_src=after_src,
                )
            except Exception:  # noqa: BLE001
                pass

        if _oracles is not None:
            try:
                oracle_report = _oracles.run_oracles(
                    file_path=file_path,
                    after_src=after_src,
                    enable_t1=os.environ.get("RC_ORACLE_T1", "1") == "1",
                    enable_t2=os.environ.get("RC_ORACLE_T2", "1") == "1",
                )
            except Exception:  # noqa: BLE001
                oracle_report = None

            if oracle_report and not oracle_report.clean:
                first = oracle_report.first_error() or oracle_report.annotations[0]
                oracle_msg = (
                    f"[reasoning-core] {'BLOCKED' if _rc_mode() != 'advise' else 'WARN'}: "
                    f"oracle failure ({first.tool})\n"
                    f"  file: {first.file_path}\n"
                    f"  line: {first.line}\n"
                    f"  reason: {first.message}\n"
                )
                oracle_reason = f"oracle_failure:{first.tool}:{first.severity}"
                oracle_extra = {
                    "oracle_elapsed_ms": oracle_report.elapsed_ms,
                    "oracle_annotations": [
                        {
                            "tool": a.tool,
                            "file_path": a.file_path,
                            "line": a.line,
                            "column": a.column,
                            "message": a.message,
                            "severity": a.severity,
                        }
                        for a in oracle_report.annotations
                    ],
                    "rc_mode": _rc_mode(),
                }
                if (
                    _rc_mode() != "advise"
                    and os.environ.get("RC_ORACLE_BLOCK") == "1"
                ):
                    audit_log.record_block(file_path)
                    pair_has_audit_row = True
                    _emit_audit(
                        tool_name=tool_name,
                        decision="blocked",
                        file_path=file_path,
                        started=started,
                        before_src=before_src,
                        after_src=after_src,
                        reason=oracle_reason,
                        signal_source="oracle",
                        retry_after_block=is_retry,
                        extra=oracle_extra,
                    )
                    _exit(2, oracle_msg)
                else:
                    # Advisory mode: emit a warning and continue.
                    sys.stderr.write(oracle_msg)
                    pair_has_audit_row = True
                    _emit_audit(
                        tool_name=tool_name,
                        decision="warn",
                        file_path=file_path,
                        started=started,
                        before_src=before_src,
                        after_src=after_src,
                        reason=oracle_reason,
                        signal_source="oracle",
                        retry_after_block=is_retry,
                        extra=oracle_extra,
                    )

        try:
            report = _post_score(file_path, before_src, after_src)
        except SidecarUnavailable as exc:
            reason_str = str(exc)
            # Phase 0: real symbolic fallback on hard cap. Do not silently
            # fail-open; run the symbolic gate chain and honor RC_MODE.
            if reason_str.startswith("hard_cap_exceeded"):
                fb = _apply_mode(
                    _symbolic_fallback(
                        file_path=file_path,
                        before_src=before_src,
                        after_src=after_src,
                        read_before_src=_read_before_src,
                    )
                )
                if fb.action == "exit_block":
                    audit_log.record_block(file_path)
                    _emit_audit(
                        tool_name=tool_name,
                        decision=fb.decision,
                        file_path=file_path,
                        started=started,
                        before_src=before_src,
                        after_src=after_src,
                        reason=fb.reason,
                        signal_source="symbolic_fallback",
                        retry_after_block=is_retry,
                        extra={**(fb.audit_extra or {}), "rc_mode": _rc_mode()},
                    )
                    _exit(2, fb.stderr)
                if fb.action == "stderr_only":
                    sys.stderr.write(fb.stderr)
                    pair_has_audit_row = True
                    _emit_audit(
                        tool_name=tool_name,
                        decision=fb.decision,
                        file_path=file_path,
                        started=started,
                        before_src=before_src,
                        after_src=after_src,
                        reason=fb.reason,
                        signal_source="symbolic_fallback",
                        retry_after_block=is_retry,
                        extra={**(fb.audit_extra or {}), "rc_mode": _rc_mode()},
                    )
                    continue
                if fb.action in ("audit_only", "continue_pair"):
                    pair_has_audit_row = True
                    if fb.action == "continue_pair":
                        audit_log.record_shadow_block(file_path)
                    _emit_audit(
                        tool_name=tool_name,
                        decision=fb.decision,
                        file_path=file_path,
                        started=started,
                        before_src=before_src,
                        after_src=after_src,
                        reason=fb.reason,
                        signal_source="symbolic_fallback",
                        retry_after_block=is_retry,
                        extra={**(fb.audit_extra or {}), "rc_mode": _rc_mode()},
                    )
                    continue
                # Clean symbolic fallback.
                pair_has_audit_row = True
                _emit_audit(
                    tool_name=tool_name,
                    decision="allowed",
                    file_path=file_path,
                    started=started,
                    before_src=before_src,
                    after_src=after_src,
                    reason="symbolic_fallback_clean",
                    signal_source="symbolic_fallback",
                    retry_after_block=is_retry,
                    extra={"rc_mode": _rc_mode()},
                )
                continue
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
                try:
                    from src.hooks import _host_env  # type: ignore
                    project_root = _host_env.project_dir()
                except Exception:  # noqa: BLE001
                    project_root = _Path(os.environ.get("RC_PROJECT_DIR") or os.getcwd())
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

        # Architectural rule engine (RC_RULE_ENGINE=1). Evaluates edits against
        # .reasoning-core/rules.yaml. Under RC_MODE=advise deny hits become
        # warnings; under copilot/autopilot they block.
        re_outcome = _apply_mode(
            _dispatch.gate_rule_engine(
                file_path=file_path,
                before_src=before_src,
                after_src=after_src,
                report=report,
            )
        )
        if re_outcome.report is not None:
            report = re_outcome.report
        if re_outcome.action == "exit_block":
            audit_log.record_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=re_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=re_outcome.reason,
                signal_source="rule_engine",
                retry_after_block=is_retry,
                extra={**(re_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            _exit(2, re_outcome.stderr)
        elif re_outcome.action == "stderr_only":
            sys.stderr.write(re_outcome.stderr)
            _emit_audit(
                tool_name=tool_name,
                decision=re_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=re_outcome.reason,
                signal_source="rule_engine",
                retry_after_block=is_retry,
                extra={**(re_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
        elif re_outcome.action in ("continue_pair", "audit_only"):
            if re_outcome.action == "continue_pair":
                pair_has_audit_row = True
                audit_log.record_shadow_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=re_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=re_outcome.reason,
                signal_source="rule_engine",
                retry_after_block=is_retry,
                extra={**(re_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            continue

        # P3 Invariant 2: cumulative_drift gate. Uses RC_DRIFT_WARN and
        # RC_DRIFT_DENY thresholds; emits reason="cumulative_drift_exceeds:..."
        # and signal_source="drift_gate". Honors RC_MODE.
        drift_outcome = _apply_mode(_dispatch.gate_drift(report=report))
        if drift_outcome.report is not None:
            report = drift_outcome.report
        if drift_outcome.action == "exit_block":
            audit_log.record_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=drift_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=drift_outcome.reason,
                signal_source="drift_gate",
                retry_after_block=is_retry,
                extra={**(drift_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            _exit(2, drift_outcome.stderr)
        elif drift_outcome.action == "stderr_only":
            sys.stderr.write(drift_outcome.stderr)
            _emit_audit(
                tool_name=tool_name,
                decision=drift_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=drift_outcome.reason,
                signal_source="drift_gate",
                retry_after_block=is_retry,
                extra={**(drift_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
        elif drift_outcome.action in ("continue_pair", "audit_only"):
            if drift_outcome.action == "continue_pair":
                pair_has_audit_row = True
                audit_log.record_shadow_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=drift_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=drift_outcome.reason,
                signal_source="drift_gate",
                retry_after_block=is_retry,
                extra={**(drift_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            continue

        # P7: Mahalanobis calibration gate (RC_CALIBRATION_ENABLED=1).
        # Default OFF. Use the tested _dispatch helper; RC_MODE=advise turns
        # the shadow anomaly into a non-blocking audit event.
        cal_outcome = _apply_mode(_dispatch.gate_calibration(report=report))
        if cal_outcome.report is not None:
            report = cal_outcome.report
        if cal_outcome.action == "stderr_only":
            sys.stderr.write(cal_outcome.stderr)
            _emit_audit(
                tool_name=tool_name,
                decision=cal_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=cal_outcome.reason,
                signal_source="calibration",
                retry_after_block=is_retry,
                extra={**(cal_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
        elif cal_outcome.action in ("continue_pair", "audit_only"):
            if cal_outcome.action == "continue_pair":
                pair_has_audit_row = True
                audit_log.record_shadow_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=cal_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=cal_outcome.reason,
                signal_source="calibration",
                retry_after_block=is_retry,
                extra={**(cal_outcome.audit_extra or {}), "rc_mode": _rc_mode()},
            )
            continue

        # PRM gate (RC_PRM_GATE=1). Shadow by default; blocks only after
        # promotion criteria are met and RC_PRM_BLOCK=1.
        if not _handle_prm_outcome(
            tool_name=tool_name,
            file_path=file_path,
            started=started,
            before_src=before_src,
            after_src=after_src,
            is_retry=is_retry,
        ):
            continue

        # Neural coherence is evidence, not an independent hard policy.  All
        # deterministic hard sources above return immediately; if execution
        # reaches here a neural regression is necessarily uncorroborated.
        if (report.get("regression_detected") and report.get("fired_conditions") is not None
                and os.environ.get("RC_NEURAL_CORROBORATED", "1") == "1"):
            advisory = dict(report)
            advisory["regression_detected"] = False
            advisory["neural_signal_mode"] = "advisory"
            _emit_audit(
                tool_name=tool_name,
                decision="warn",
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=advisory,
                reason="neural_uncorroborated_advisory",
                signal_source="neural",
                retry_after_block=is_retry,
                extra={"rc_mode": _rc_mode(), "neural_signal_mode": "advisory"},
            )
            report = advisory
            pair_has_audit_row = True

        # Final SSM/aggregate regression gate. Honor RC_MODE: advise warns,
        # copilot/autopilot block.
        reg_outcome = _apply_mode(
            _dispatch.gate_regression(
                report=report, file_path=file_path, is_retry=is_retry
            )
        )
        if reg_outcome.action == "exit_block":
            audit_log.record_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=reg_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=reg_outcome.reason,
                retry_after_block=is_retry,
                extra={"rc_mode": _rc_mode()},
            )
            _exit(2, reg_outcome.stderr)
            return  # pragma: no cover
        if reg_outcome.action == "stderr_only":
            sys.stderr.write(reg_outcome.stderr)
            _emit_audit(
                tool_name=tool_name,
                decision=reg_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=reg_outcome.reason,
                retry_after_block=is_retry,
                extra={"rc_mode": _rc_mode()},
            )
            continue
        if reg_outcome.action in ("continue_pair", "audit_only"):
            if reg_outcome.action == "continue_pair":
                pair_has_audit_row = True
                audit_log.record_shadow_block(file_path)
            _emit_audit(
                tool_name=tool_name,
                decision=reg_outcome.decision,
                file_path=file_path,
                started=started,
                before_src=before_src,
                after_src=after_src,
                report=report,
                reason=reg_outcome.reason,
                retry_after_block=is_retry,
                extra={"rc_mode": _rc_mode()},
            )
            continue


    # All edits cleared.
    # If any pair landed shadow_blocked or was handled by symbolic fallback,
    # the decision row has already been emitted — do NOT also emit `allowed`
    # for the same edit (double-count).
    if pair_has_audit_row:
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
        extra={"rc_mode": _rc_mode()},
    )
    _exit(0)


if __name__ == "__main__":
    main()
