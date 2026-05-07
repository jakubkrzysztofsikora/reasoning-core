"""Per-gate dispatch chain extracted from `pre_edit_guard.py` (Phase 0 commit 2).

Each `gate_*` returns a `GateOutcome` describing the decision the orchestrator
should apply. The orchestrator (`pre_edit_guard.main`) owns side-effects:
audit emission, audit_log block recording, stderr writes, and process exit.
This avoids a `_dispatch -> audit_log` import cycle and keeps existing
subprocess-based tests behavior-byte-identical.

Action vocabulary (string sentinels on `GateOutcome.action`):

  - "pass"            -> no decision; continue to next gate
  - "exit"            -> orchestrator should call `_exit(code, stderr)`
  - "exit_block"      -> orchestrator should `audit_log.record_block(fp)`
                          + emit blocked audit + `_exit(code, stderr)`
  - "exit_allow"      -> orchestrator should emit allowed_via_override audit
                          + `_exit(0)`
  - "continue_pair"   -> shadow path; orchestrator should
                          `audit_log.record_shadow_block(fp)` + emit
                          shadow_blocked audit + `continue` outer pair loop
  - "mutate_report"   -> orchestrator should replace `report` with
                          `outcome.report` and continue gate chain
  - "stderr_only"     -> orchestrator should write `outcome.stderr` to
                          stderr (advisory) and continue chain
  - "fall_through"    -> magic-comment self-introduced edge case;
                          orchestrator emits override_declined audit and
                          continues into the scoring path

Each per-gate function imports its own dependencies lazily so a missing
helper module (degraded environment) does not break the orchestrator.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class GateOutcome:
    """Result of a per-gate evaluation. See module docstring for vocabulary."""

    action: str = "pass"
    code: int = 0
    stderr: str = ""
    decision: str = ""  # audit `decision` field
    reason: str = ""    # audit `reason` field
    signal_source: str = ""
    report: Optional[Dict[str, Any]] = None  # for mutate_report
    audit_extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pre-score gates (run once before the SSM /score call).
# ---------------------------------------------------------------------------

def gate_kill_switch_and_magic(
    *,
    tool_name: str,
    tool_input: Dict[str, Any],
    file_path: str,
    read_before_src,
) -> GateOutcome:
    """Layer 2 — operator override path (kill switches + magic comments).

    Mirrors lines 333-386 of pre-Phase-0 pre_edit_guard. Returns:
      - exit_allow: kill switch / magic comment grants bypass
      - fall_through: agent self-introduced directive — decline override but
        do not bypass; orchestrator must emit override_declined and proceed
      - pass: no override active
    """
    try:
        import _kill_switches  # type: ignore
        import _magic_comments  # type: ignore
    except ImportError:
        return GateOutcome(action="pass")

    if _kill_switches is not None:
        if (
            _kill_switches.is_disabled_globally()
            or _kill_switches.consume_bypass_next()
            or _kill_switches.is_file_skipped(file_path)
        ):
            return GateOutcome(
                action="exit_allow",
                decision="allowed_via_override",
                reason="kill_switch_or_bypass_next",
            )

    if _magic_comments is None:
        return GateOutcome(action="pass")

    before_for_directive = read_before_src(file_path)
    directive_before = _magic_comments.parse(before_for_directive)
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
        return GateOutcome(
            action="fall_through",
            decision="override_declined",
            reason=f"magic_comment_self_introduced:{directive_after.name}",
        )
    if _magic_comments.bypasses_all(directive_before):
        return GateOutcome(
            action="exit_allow",
            decision="allowed_via_override",
            reason=f"magic_comment:{directive_before.name}:{directive_before.reason}",
        )
    return GateOutcome(action="pass")


def gate_lang_lock(*, file_path: str, read_before_src) -> GateOutcome:
    """Layer 3 — language fingerprint lock (P3 Invariant 1).

    Returns either an exit_block, a continue_pair (shadow), or pass.
    """
    if (
        os.environ.get("RC_LANG_LOCK") != "1"
        or os.environ.get("RC_LANG_OVERRIDE") == "1"
    ):
        return GateOutcome(action="pass")

    try:
        import _session_manifest  # type: ignore

        try:
            import _magic_comments  # type: ignore
        except ImportError:
            _magic_comments = None  # type: ignore
        try:
            import _shadow_mode  # type: ignore
        except ImportError:
            _shadow_mode = None  # type: ignore

        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        task_spec = os.environ.get("RC_TASK_SPEC") or ""
        key = _session_manifest.manifest_key(cwd, task_spec)
        mani = _session_manifest.load(key)
        lang_skip = (
            _magic_comments is not None
            and _magic_comments.bypasses(
                _magic_comments.parse(read_before_src(file_path)),
                "lang",
            )
        )
        if mani and not lang_skip and not _session_manifest.is_path_allowed(mani, file_path):
            shadow = _shadow_mode.is_active() if _shadow_mode else False
            if shadow:
                return GateOutcome(
                    action="continue_pair",
                    decision="shadow_blocked",
                    reason="language_fingerprint_violation",
                    signal_source="lang_lock",
                )
            declared = mani.get("declared_language")
            return GateOutcome(
                action="exit_block",
                code=2,
                stderr=(
                    f"[hybrid-reasoner] BLOCKED: language fingerprint violation\n"
                    f"  file: {file_path}\n"
                    f"  declared: {declared}"
                ),
                decision="blocked",
                reason="language_fingerprint_violation",
                signal_source="lang_lock",
            )
    except Exception:  # noqa: BLE001
        pass
    return GateOutcome(action="pass")


def _resolve_plan_path() -> Optional[Path]:
    """PLAN.md resolution precedence: RC_RUN_DIR > CLAUDE_PROJECT_DIR > cwd.

    Returns the first existing PLAN.md or None. Used by gate_plan_grounding.
    """
    for env_key in ("RC_RUN_DIR", "CLAUDE_PROJECT_DIR"):
        base = os.environ.get(env_key)
        if base:
            p = Path(base) / "PLAN.md"
            if p.exists():
                return p
    p = Path.cwd() / "PLAN.md"
    return p if p.exists() else None


def gate_plan_grounding(*, file_path: str) -> GateOutcome:
    """Iter-3 lever — plan-impl coupling audit signal.

    Modes (RC_PLAN_GROUNDING):
      - unset / "0" : disabled, gate is no-op (default).
      - "1"         : warn — orchestrator emits stderr advisory + audit event
                      tagged signal_source=plan_grounding decision=warn.
      - "2"         : block — orchestrator records audit block + exit code 2.

    PLAN.md itself is always allowed (basename match) so the gate cannot
    block plan revision. Path-match uses ``os.path.normpath`` + suffix
    against the full reference string (engineer-flagged tightening over the
    naive ``Path(fp).name in refs``).
    """
    mode = os.environ.get("RC_PLAN_GROUNDING", "0").strip()
    if mode not in ("1", "2"):
        return GateOutcome()
    if not file_path:
        return GateOutcome()
    basename = os.path.basename(file_path).lower()
    if basename == "plan.md" or basename.endswith(".plan.md"):
        return GateOutcome()
    plan_path = _resolve_plan_path()
    if plan_path is None:
        return GateOutcome(reason="no_plan_md")
    # Lazy import: _dispatch.py does NOT inject src/hooks into sys.path
    # itself. Import inside the function body so callers that have already
    # set sys.path (pre_edit_guard.py:33-36) resolve correctly, while a
    # cold direct import of _dispatch from elsewhere doesn't crash.
    try:
        from _plan_paths import distinct_file_paths  # type: ignore
        plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
        refs = distinct_file_paths(plan_text)
    except (OSError, ImportError):
        return GateOutcome(reason="plan_unreadable")
    norm = os.path.normpath(file_path)
    for ref in refs:
        if norm.endswith(os.path.normpath(ref)):
            return GateOutcome(reason="in_plan")
    audit_extra: Dict[str, Any] = {
        "plan_path": str(plan_path),
        "plan_refs_count": len(refs),
    }
    if mode == "1":
        return GateOutcome(
            action="stderr_only",
            stderr=(
                f"[reasoning-core] WARN: edit drifts from plan — "
                f"{file_path} not in {plan_path} ({len(refs)} files in plan)\n"
            ),
            decision="warn",
            reason="plan_impl_drift",
            signal_source="plan_grounding",
            audit_extra=audit_extra,
        )
    return GateOutcome(
        action="exit_block",
        code=2,
        stderr=(
            f"[reasoning-core] BLOCKED: plan_impl_drift — {file_path} not in PLAN.md.\n"
            f"  Update PLAN.md to include this file, or set RC_PLAN_GROUNDING=1 for warn-only.\n"
        ),
        decision="blocked",
        reason="plan_impl_drift",
        signal_source="plan_grounding",
        audit_extra=audit_extra,
    )


# ---------------------------------------------------------------------------
# Post-score gates (run on each (before, after) pair after /score).
# ---------------------------------------------------------------------------

def gate_mock_detector(
    *,
    report: Dict[str, Any],
    file_path: str,
    after_src: str,
    read_before_src,
) -> GateOutcome:
    """P1 mock-detector gate. Returns mutate_report (regression flag set) or pass.

    Mirrors lines 507-536 of pre-Phase-0 pre_edit_guard.
    """
    try:
        try:
            import _magic_comments  # type: ignore
        except ImportError:
            _magic_comments = None  # type: ignore

        mock_skip = False
        if _magic_comments is not None:
            pre_directive = _magic_comments.parse(read_before_src(file_path))
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

            project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
            if _mock_detector.is_likely_mocked(after_src, project_root):
                auth = _mock_detector.integration_authenticity(after_src, project_root)
                new_report = dict(report)
                new_report["regression_detected"] = True
                new_report["mock_detector_triggered"] = True
                new_report["signal_source"] = "mock_heuristic"
                new_report["integration_authenticity"] = auth
                new_report["human_summary"] = (
                    report.get("human_summary", "")
                    + f" | mock-detector flagged: integration_authenticity={auth:.2f}"
                )
                return GateOutcome(action="mutate_report", report=new_report)
    except Exception:  # noqa: BLE001
        pass
    return GateOutcome(action="pass")


def gate_drift(*, report: Dict[str, Any]) -> GateOutcome:
    """P3 Invariant 2 — cumulative_drift gate.

    Returns: exit_block | continue_pair (shadow) | stderr_only (warn) | pass.
    """
    if not isinstance(report, dict):
        return GateOutcome(action="pass")
    if os.environ.get("RC_DRIFT_OVERRIDE") == "1":
        return GateOutcome(action="pass")
    try:
        try:
            import _shadow_mode  # type: ignore
        except ImportError:
            _shadow_mode = None  # type: ignore

        drift = report.get("cumulative_drift")
        if not isinstance(drift, (int, float)):
            return GateOutcome(action="pass")
        drift_warn = float(os.environ.get("RC_DRIFT_WARN", "4.0"))
        drift_deny = float(os.environ.get("RC_DRIFT_DENY", "6.0"))
        if drift > drift_deny:
            reason = f"cumulative_drift_exceeds:{drift:.2f}>{drift_deny}"
            if _shadow_mode is not None and _shadow_mode.is_active():
                return GateOutcome(
                    action="continue_pair",
                    decision="shadow_blocked",
                    reason=reason,
                    signal_source="drift_gate",
                )
            return GateOutcome(
                action="exit_block",
                code=2,
                stderr=(
                    f"[hybrid-reasoner] BLOCKED: cumulative_drift {drift:.2f} "
                    f"exceeds threshold {drift_deny:.2f}"
                ),
                decision="blocked",
                reason=reason,
                signal_source="drift_gate",
            )
        if drift > drift_warn:
            return GateOutcome(
                action="stderr_only",
                stderr=(
                    f"[hybrid-reasoner] WARN: cumulative_drift {drift:.2f} "
                    f"exceeds warn threshold {drift_warn:.2f}\n"
                ),
            )
    except (TypeError, ValueError):
        pass
    return GateOutcome(action="pass")


def gate_calibration(*, report: Dict[str, Any]) -> GateOutcome:
    """P7 Mahalanobis calibration gate (RC_CALIBRATION_ENABLED=1).

    Always returns mutate_report when calibration runs (to inject the
    `calibration` key); may also return continue_pair (shadow) or
    stderr_only (advisory). Pass when gate disabled or unavailable.
    """
    try:
        import _calibration_gate  # type: ignore
    except ImportError:
        return GateOutcome(action="pass")
    if not (
        _calibration_gate is not None
        and isinstance(report, dict)
        and _calibration_gate.is_enabled()
    ):
        return GateOutcome(action="pass")

    try:
        rv = report.get("risk_vector") or []
        fk = report.get("file_kind")
        calibration_result = _calibration_gate.evaluate(rv, file_kind=fk)
    except Exception:  # noqa: BLE001
        calibration_result = None
    if calibration_result is None:
        return GateOutcome(action="pass")

    new_report = dict(report)
    new_report["calibration"] = calibration_result

    if calibration_result.get("anomaly") and not new_report.get("regression_detected"):
        try:
            import _shadow_mode  # type: ignore
        except ImportError:
            _shadow_mode = None  # type: ignore
        if _shadow_mode is not None and _shadow_mode.is_active():
            return GateOutcome(
                action="continue_pair",
                report=new_report,
                decision="shadow_blocked",
                reason=(
                    f"calibration_anomaly:score={calibration_result['score']:.2f}"
                    f">thr={calibration_result['threshold']:.2f}"
                    f":kind={calibration_result['kind_used']}"
                ),
                signal_source="calibration",
            )
        # Advisory only — log + mutate.
        return GateOutcome(
            action="stderr_only",
            report=new_report,
            stderr=(
                f"[hybrid-reasoner] calibration anomaly "
                f"(score={calibration_result['score']:.2f} > "
                f"thr={calibration_result['threshold']:.2f}, "
                f"kind={calibration_result['kind_used']}) — advisory only\n"
            ),
        )
    return GateOutcome(action="mutate_report", report=new_report)


def gate_regression(*, report: Dict[str, Any]) -> GateOutcome:
    """Final SSM regression gate.

    Returns exit_block (with format_block stderr filled by orchestrator) or
    continue_pair (shadow) or pass.
    """
    if not (isinstance(report, dict) and report.get("regression_detected") is True):
        return GateOutcome(action="pass")
    try:
        import _shadow_mode  # type: ignore
    except ImportError:
        _shadow_mode = None  # type: ignore
    if _shadow_mode is not None and _shadow_mode.is_active():
        return GateOutcome(
            action="continue_pair",
            decision="shadow_blocked",
            reason="regression_detected_shadow",
        )
    # Orchestrator fills the stderr via _format_block — leave stderr empty
    # as a sentinel meaning "use _format_block".
    return GateOutcome(
        action="exit_block",
        code=2,
        stderr="",  # sentinel: orchestrator must call _format_block
        decision="blocked",
        reason="regression_detected",
    )
