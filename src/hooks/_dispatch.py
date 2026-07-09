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
  - "audit_only"      -> orchestrator emits audit event (decision/reason/
                          signal_source from outcome) and continues chain
                          without stderr or exit. Used for observability
                          of "gate was active but precondition missing"
                          (e.g. RC_PLAN_GROUNDING=1 with no PLAN.md).
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
from typing import Any, Dict, List, Optional


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

def gate_lang_lock(*, file_path: str, read_before_src) -> GateOutcome:
    """Layer 3 — language fingerprint lock (P3 Invariant 1).

    Returns either an exit_block, a continue_pair (shadow), a stderr_only
    (warn in RC_MODE=advise), or pass.
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
            file_lang = _session_manifest.language_for_path(file_path)
            stderr = (
                f"[hybrid-reasoner] BLOCKED: language fingerprint violation\n"
                f"  file: {file_path}\n"
                f"  declared: {declared}"
            )
            reason = "language_fingerprint_violation"
            if os.environ.get("RC_MODE", "advise").strip().lower() == "advise":
                return GateOutcome(
                    action="stderr_only",
                    code=0,
                    stderr=(
                        f"[hybrid-reasoner] WARN: language fingerprint violation\n"
                        f"  file: {file_path}\n"
                        f"  declared: {declared}\n"
                        f"  file_lang: {file_lang}"
                    ),
                    decision="warn",
                    reason=reason,
                    signal_source="lang_lock",
                )
            return GateOutcome(
                action="exit_block",
                code=2,
                stderr=stderr,
                decision="blocked",
                reason=reason,
                signal_source="lang_lock",
            )
    except Exception:  # noqa: BLE001
        pass
    return GateOutcome(action="pass")


def _resolve_plan_path() -> Optional[Path]:
    """PLAN.md resolution precedence: RC_RUN_DIR > host project_dir > cwd.

    Returns the first existing PLAN.md or None. Used by gate_plan_grounding.
    """
    rc_run = os.environ.get("RC_RUN_DIR")
    if rc_run:
        p = Path(rc_run) / "PLAN.md"
        if p.exists():
            return p
    try:
        from src.hooks import _host_env  # type: ignore
        host_root = _host_env.project_dir()
    except Exception:  # noqa: BLE001
        host_root = Path(os.environ.get("RC_PROJECT_DIR") or "")
    if host_root and host_root.exists():
        p = host_root / "PLAN.md"
        if p.exists():
            return p
    p = Path.cwd() / "PLAN.md"
    return p if p.exists() else None


def gate_plan_grounding(
    *,
    file_path: str,
    after_src: Optional[str] = None,
    path_check: bool = True,
) -> GateOutcome:
    """Iter-3 lever — plan-impl coupling audit signal.

    Phase 1 upgrade: this gate now evaluates a machine-readable contract
    derived from ``PLAN.md`` or an explicit ``.reasoning-core/contract.yaml``.
    Path checks remain cheap and run on every call. Import/invariant checks
    run only when ``after_src`` is supplied (the orchestrator calls this
    once per edit pair after extracting the proposed source).

    ``path_check=False`` skips the path-level evaluation so the orchestrator
    can evaluate imports/invariants in the pair loop without re-emitting the
    path-violation message.

    Modes (RC_PLAN_GROUNDING):
      - unset / "0" : disabled, gate is no-op (default).
      - "1"         : warn — orchestrator emits stderr advisory + audit event
                      tagged signal_source=plan_grounding decision=warn.
      - "2"         : block — orchestrator records audit block + exit code 2.

    PLAN.md itself is always allowed (basename match) so the gate cannot
    block plan revision.
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
        # B3 fix (sweep round-5): mode is active but PLAN.md is absent.
        # Emit an audit event so the eval aggregator can distinguish
        # "lever was active and ran cleanly" from "lever silently no-oped
        # because the spawner forgot to write PLAN.md". Without this,
        # missing-PLAN.md runs are indistinguishable from perfectly-grounded
        # runs in audit data.
        return GateOutcome(
            action="audit_only",
            decision="audit_only",
            reason="no_plan_md",
            signal_source="plan_grounding",
        )

    # Lazy import: _dispatch.py does NOT inject src/hooks into sys.path
    # itself. Import inside the function body so callers that have already
    # set sys.path (pre_edit_guard.py:33-36) resolve correctly, while a
    # cold direct import of _dispatch from elsewhere doesn't crash.
    try:
        from _plan_contract import Contract  # type: ignore

        contract = Contract.load(
            project_root=str(plan_path.parent),
            plan_text=plan_path.read_text(encoding="utf-8", errors="replace"),
            plan_path=plan_path,
        )
    except (OSError, ImportError) as exc:
        # Distinguish ImportError (load bug) from OSError (operator state)
        # so the audit reason is diagnostic-grade.
        reason = (
            "plan_contract_unavailable"
            if isinstance(exc, ImportError)
            else "plan_unreadable"
        )
        return GateOutcome(
            action="audit_only",
            decision="audit_only",
            reason=reason,
            signal_source="plan_grounding",
        )

    # Path check first (T0 cheap).
    path_violation = contract.check_path(file_path) if path_check else None

    # Import/invariant checks only when after_src is available.
    rich_violations: List[Any] = []
    if after_src is not None:
        rich_violations.extend(contract.check_imports(file_path, after_src))
        rich_violations.extend(contract.check_invariants(file_path, after_src))

    first_deny = contract.first_deny(rich_violations)

    audit_extra: Dict[str, Any] = {
        "plan_path": str(plan_path),
        "contract_source": contract.source,
        "contract_version": contract.version,
    }

    # Backward-compatible behavior: a plan-derived contract with only path
    # rules uses the original reason/message shape that existing tests assert.
    plan_derived_only = (
        path_check
        and contract.source == str(plan_path)
        and not rich_violations
    )

    if path_violation is None and first_deny is None:
        return GateOutcome(
            reason="in_plan",
            audit_extra=audit_extra,
        )

    # Rich violations (imports/invariants) take precedence for the block
    # message, but path violations still use the legacy shape when the
    # contract is a simple PLAN.md derivation.
    violation = first_deny or path_violation
    if violation is None:
        return GateOutcome(reason="in_plan", audit_extra=audit_extra)

    if plan_derived_only and violation.kind == "path":
        audit_extra["plan_refs_count"] = len(contract.allowed_paths)
        if mode == "1":
            return GateOutcome(
                action="stderr_only",
                stderr=(
                    f"[reasoning-core] WARN: edit drifts from plan — "
                    f"{file_path} not in {plan_path} ({len(contract.allowed_paths)} files in plan)\n"
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

    # Rich-contract violation messaging.
    audit_extra["contract_violations"] = [
        v.to_dict() for v in ([path_violation] if path_violation else []) + rich_violations
        if v is not None
    ]
    severity = violation.severity
    if mode == "1" or severity == "warn":
        return GateOutcome(
            action="stderr_only",
            stderr=(
                f"[reasoning-core] WARN: contract violation — "
                f"{violation.message} ({violation.rule_id})\n"
            ),
            decision="warn",
            reason=f"contract_violation:{violation.kind}:{violation.rule_id}",
            signal_source="plan_grounding",
            audit_extra=audit_extra,
        )
    return GateOutcome(
        action="exit_block",
        code=2,
        stderr=(
            f"[reasoning-core] BLOCKED: contract violation — "
            f"{violation.message} ({violation.rule_id})\n"
        ),
        decision="blocked",
        reason=f"contract_violation:{violation.kind}:{violation.rule_id}",
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

            try:
                from src.hooks import _host_env  # type: ignore
                project_root = _host_env.project_dir()
            except Exception:  # noqa: BLE001
                project_root = Path(os.environ.get("RC_PROJECT_DIR") or os.getcwd())
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


def gate_rule_engine(
    *,
    file_path: str,
    before_src: str,
    after_src: str,
    report: Dict[str, Any],
) -> GateOutcome:
    """Architectural rule engine gate (RC_RULE_ENGINE=1).

    Evaluates the edit against rules in .reasoning-core/rules.yaml.
    Returns exit_block on deny hits, stderr_only on warn hits,
    continue_pair on shadow hits, pass when clean or disabled.
    """
    if os.environ.get("RC_RULE_ENGINE") != "1":
        return GateOutcome(action="pass")

    try:
        from src.hooks import _rule_engine
    except ImportError as exc:
        # The gate is explicitly enabled (RC_RULE_ENGINE=1). A packaging or
        # sys.path regression must not silently disable a security control.
        # Fail closed; ``RC_RULE_ENGINE_LENIENT=1`` is the documented escape
        # hatch for operators who want a soft-degrade during incident response.
        if os.environ.get("RC_RULE_ENGINE_LENIENT") == "1":
            return GateOutcome(
                action="stderr_only",
                stderr=f"[rule_engine] lenient mode: import failed: {exc}\n",
                signal_source="rule_engine_lenient",
            )
        return GateOutcome(
            action="exit_block",
            code=2,
            stderr=(
                f"[rule_engine] BLOCKED: rule engine import failed ({exc}). "
                f"RC_RULE_ENGINE=1 is set; refusing to evaluate edits without "
                f"the gate. Set RC_RULE_ENGINE_LENIENT=1 to soft-degrade or "
                f"unset RC_RULE_ENGINE to disable the gate.\n"
            ),
            decision="rule_engine_unavailable",
            reason="rule_engine_import_error",
            signal_source="rule_engine",
        )

    # Detect language from file extension
    lang = _detect_language(file_path)

    # Find project root
    project_root = os.environ.get("RC_PROJECT_DIR") or os.getcwd()

    try:
        rules = _rule_engine.load_rules(project_root)
    except _rule_engine.RuleEngineError as exc:
        if os.environ.get("RC_RULE_ENGINE_LENIENT") == "1":
            return GateOutcome(
                action="stderr_only",
                stderr=f"[rule_engine] lenient mode: {exc}\n",
                signal_source="rule_engine_lenient",
            )
        return GateOutcome(
            action="exit_block",
            decision="rule_engine_error",
            reason=f"rule_engine_schema_error:{exc}",
            signal_source="rule_engine",
        )

    if not rules:
        return GateOutcome(action="pass")

    # The rule engine matches ``scope`` globs against both absolute and
    # repo-relative forms, but it relies on ``CLAUDE_PROJECT_DIR`` to derive
    # the relative form. Pass ``project_root`` via the env var so harnesses
    # that set ``RC_PROJECT_DIR`` instead still get correct scope matching.
    prior = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = project_root
    try:
        hits = _rule_engine.evaluate_edit(
            file_path, before_src or "", after_src or "", lang, rules,
        )
    finally:
        if prior is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = prior

    if not hits:
        return GateOutcome(action="pass")

    # Serialize hits to report
    new_report = dict(report)
    new_report["rule_hits"] = [h.to_dict() for h in hits]

    # Check for deny hits
    deny_hits = [h for h in hits if h.severity == "deny"]
    if deny_hits:
        fired = new_report.get("fired_conditions", [])
        if isinstance(fired, list):
            fired = list(fired)
            if "rule_engine" not in fired:
                fired.append("rule_engine")
            new_report["fired_conditions"] = fired
        new_report["regression_detected"] = True

        try:
            import _shadow_mode  # type: ignore
        except ImportError:
            _shadow_mode = None  # type: ignore
        if _shadow_mode is not None and _shadow_mode.is_active():
            return GateOutcome(
                action="continue_pair",
                report=new_report,
                decision="shadow_blocked",
                reason=f"rule_engine:{len(deny_hits)} deny hits",
                signal_source="rule_engine",
            )
        return GateOutcome(
            action="exit_block",
            code=2,
            stderr=(
                f"[rule_engine] BLOCKED: {deny_hits[0].rule_id} — "
                f"{deny_hits[0].message}\n"
            ),
            report=new_report,
            decision="rule_engine",
            reason=f"rule_engine:{deny_hits[0].rule_id}:{deny_hits[0].message}",
            signal_source="rule_engine",
        )

    # Warn hits
    warn_hits = [h for h in hits if h.severity == "warn"]
    if warn_hits:
        msg = _rule_engine.format_hits(warn_hits)
        return GateOutcome(
            action="stderr_only",
            report=new_report,
            stderr=f"[rule_engine] {msg}\n",
        )

    # Shadow hits
    shadow_hits = [h for h in hits if h.severity == "shadow"]
    if shadow_hits:
        return GateOutcome(
            action="continue_pair",
            report=new_report,
            decision="shadow_advisory",
            reason=f"rule_engine:{len(shadow_hits)} shadow hits",
            signal_source="rule_engine",
        )

    return GateOutcome(action="pass", report=new_report)


def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
    }
    return mapping.get(ext, "unknown")


def gate_regression(
    *, report: Dict[str, Any], file_path: str = "", is_retry: bool = False
) -> GateOutcome:
    """Final SSM regression gate.

    Returns exit_block (stderr pre-formatted via _format_block), continue_pair
    (shadow), or pass. When file_path is omitted, stderr falls back to a
    reason-only message so advise-mode downgrades still produce a warning.
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
    stderr = ""
    if file_path:
        try:
            from _block_format import format_block as _format_block  # type: ignore
            stderr = _format_block(file_path, report, is_retry=is_retry)
        except Exception:  # noqa: BLE001
            stderr = ""
    if not stderr:
        summary = report.get("human_summary") or ""
        stderr = f"[hybrid-reasoner] BLOCKED: regression detected"
        if summary:
            stderr += f" — {summary}"
        stderr += "\n"
    return GateOutcome(
        action="exit_block",
        code=2,
        stderr=stderr,
        decision="blocked",
        reason="regression_detected",
    )


# ---------------------------------------------------------------------------
# PRM measurement gate (audit 2026-06-01 §B1).
# Default OFF until training data lands; emits audit events but never blocks.
# ---------------------------------------------------------------------------

def gate_prm(
    *,
    file_path: str,
    before_src: str,
    after_src: str,
    plan_text: Optional[str],
) -> GateOutcome:
    """Score (plan_claim, diff_hunk) via gen_client.score_plan_grounding.

    PRM gate. Behavior depends on ``RC_PRM_GATE`` and ``RC_PRM_BLOCK``:
      - RC_PRM_GATE unset/0   : gate is no-op.
      - RC_PRM_GATE=1         : emit audit events (shadow mode).
      - RC_PRM_BLOCK=1        : block when score < RC_PRM_THRESHOLD if the
                                promotion criteria are met; otherwise shadow.

    Default threshold is 0.25. Promotion requires ≥2 weeks, ≥1000 events,
    ≥5 distinct repo installs (tracked in
    ``~/.cache/reasoning-core/prm-shadow-state.jsonl``).
    """
    if os.environ.get("RC_PRM_GATE", "0") != "1":
        return GateOutcome()  # action="pass", no signal
    if not plan_text:
        return GateOutcome(
            action="continue_pair",
            decision="audit_only",
            reason="prm_skip:no_plan_md",
            signal_source="prm",
        )
    import difflib
    diff = "\n".join(difflib.unified_diff(
        (before_src or "").splitlines(),
        (after_src or "").splitlines(),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    ))
    diff_hunk = diff[:1500] if diff else f"(no-op edit on {file_path})"
    plan_claim = plan_text[:500]
    try:
        try:
            from src import gen_client as _gc  # type: ignore
        except ImportError:
            import gen_client as _gc  # type: ignore
        verdict = _gc.score_plan_grounding(plan_claim, diff_hunk)
    except Exception:  # noqa: BLE001
        return GateOutcome(
            action="continue_pair",
            decision="audit_only",
            reason="prm_unavailable",
            signal_source="prm",
        )
    if not isinstance(verdict, dict) or not verdict:
        return GateOutcome(
            action="continue_pair",
            decision="audit_only",
            reason="prm_unavailable",
            signal_source="prm",
        )
    yes = sum(1 for v in verdict.values() if v == 1)
    total = len(verdict)
    score = (yes / total) if total else 0.0

    audit_extra = {
        "prm_score": float(score),
        "prm_yes": yes,
        "prm_total": total,
    }

    try:
        threshold = float(os.environ.get("RC_PRM_THRESHOLD", "0.25"))
    except ValueError:
        threshold = 0.25

    if score < threshold:
        # Blocking only after promotion criteria are met.
        try:
            from _prm_promotion import promotion_status  # type: ignore

            promo = promotion_status()
            audit_extra["prm_promoted"] = promo.promoted
            audit_extra["prm_promo_reason"] = promo.reason
        except Exception:  # noqa: BLE001
            promo = None
            audit_extra["prm_promoted"] = False

        if os.environ.get("RC_PRM_BLOCK") == "1" and promo is not None and promo.promoted:
            return GateOutcome(
                action="exit_block",
                code=2,
                stderr=(
                    f"[reasoning-core] BLOCKED: PRM score {score:.2f} below "
                    f"threshold {threshold:.2f}\n"
                ),
                decision="blocked",
                reason=f"prm_score:{score:.2f}<{threshold:.2f}",
                signal_source="prm",
                audit_extra=audit_extra,
            )

        # Shadow / advisory path.
        return GateOutcome(
            action="stderr_only",
            stderr=(
                f"[reasoning-core] WARN: PRM score {score:.2f} below "
                f"threshold {threshold:.2f} (shadow)\n"
            ),
            decision="warn",
            reason=f"prm_shadow:{score:.2f}<{threshold:.2f}",
            signal_source="prm",
            audit_extra=audit_extra,
        )

    return GateOutcome(
        action="continue_pair",
        decision="allowed",
        reason=f"prm_score={score:.2f}",
        signal_source="prm",
        audit_extra=audit_extra,
    )
