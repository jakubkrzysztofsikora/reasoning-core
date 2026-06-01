#!/usr/bin/env python3
"""SessionStart-hook overlay (RC_BEST_EFFORT_SPEC) — iter-3 lever.

When ``RC_BEST_EFFORT_SPEC=1`` is exported into the session environment,
this hook injects a single-sentence system-context overlay via the
``hookSpecificOutput.additionalContext`` envelope that Claude Code reads.

Default off — env var unset emits nothing and exits 0. The hook is
stateless: no manifest reads, no audit emission, no sidecar dependency.

Wording is deliberately minimal (single-factor for iter-4 attribution per
the LLM-scientist review of plan v2): it removes the agent's implicit
license to ship ``DIVERGENCES.md`` alone, but does NOT prescribe a
substitution recipe. Iter-4 can ablate the substitution-recipe variant
separately by widening this string under a different env-var.

Eval team opts in by adding to their own ``settings.local.json``:

    {
      "hooks": {
        "SessionStart": [
          {"hooks": [{"type": "command",
                       "command": "python3 ${RC_REPO}/src/hooks/session_start_best_effort.py",
                       "timeout": 10}]}
        ]
      }
    }

JSON envelope shape mirrors ``session_resume_inject._emit_additional_context``
which is the production-validated mechanism for SessionStart context
injection in this repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

# Overlay v3 (round-7 hotfix): the v2 carve-out was gamed live in iter-3 —
# B/P0 forfeited the smoke test in 53s by reading "missing fixtures + Docker
# stack" as "contract requires no production change" and shipping a 1-line
# PLAN.md only. v3 closes that escape hatch by enumerating the no-op
# condition explicitly (work-already-done) and explicitly REJECTING
# missing-infrastructure as a no-op trigger — for that case the original
# rule applies (best-effort stub + DIVERGENCES.md naming the gap).
_OVERLAY = (
    "Never ship a DIVERGENCES.md alone. The only exception is when the "
    "requested change has already been applied upstream (e.g. a rename "
    "already done in a prior commit, or a no-op cutover where target state "
    "already matches) — in that case ship a one-line PLAN.md stating which "
    "prior commit applied the change and produce no other artifact. "
    "Missing infrastructure (Docker, fixtures, DB, sandbox endpoints) is "
    "NOT a no-op condition — for that, ship a best-effort compilable stub "
    "against the closest available contract plus a DIVERGENCES.md naming "
    "the missing infra and the substitution made."
)


def _overlay_sha() -> str:
    """Stable hash of the overlay text — pinnable in audit receipts."""
    return hashlib.sha256(_OVERLAY.encode("utf-8")).hexdigest()[:12]


def _emit_envelope() -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _OVERLAY,
        }
    }
    sys.stdout.write(json.dumps(out))


def _emit_receipt(*, fired: bool, env_value: str) -> None:
    """Write one audit_log line per invocation — symmetric receipt.

    Reviewer-mandated (scientist + newsletter v4): without an audit-visible
    receipt, a reviewer cannot prove the lever fired vs. just being set in
    env. Symmetric receipt = audit line emitted on BOTH fire AND skip paths.
    Aggregator can confirm Setup B has N receipts with fired=True per run,
    Setup A has zero (or N skip receipts with fired=False), per docs/iter3-levers.md.
    """
    try:
        # audit_log lives next to this script; add hooks dir to sys.path so
        # the import resolves without depending on caller's path injection.
        _hooks_dir = os.path.dirname(os.path.abspath(__file__))
        if _hooks_dir not in sys.path:
            sys.path.insert(0, _hooks_dir)
        import audit_log  # type: ignore
    except Exception:  # noqa: BLE001
        return  # never break SessionStart on audit failure
    try:
        audit_log.append_event(audit_log.new_event(
            tool_name="SessionStart",
            decision="injected" if fired else "skipped",
            file_path=None,
            signal_source="best_effort_spec",
            reason=f"rc_best_effort_spec={env_value or '(unset)'}",
            overlay_sha=_overlay_sha() if fired else None,
        ))
    except Exception:  # noqa: BLE001
        pass


def _maybe_scaffold_plan() -> None:
    """Best-effort PLAN.md scaffold when RC_PLAN_GROUNDING is enabled.

    Audit 2026-06-01 §B4: repos without PLAN.md silently degrade to
    audit_only:no_plan_md. Generate a stub on first contact. Honors
    RC_NO_PLAN_SCAFFOLD=1. Never raises into the SessionStart hook.
    """
    pg = os.environ.get("RC_PLAN_GROUNDING", "0")
    if pg not in ("1", "2"):
        return
    project_dir = (
        os.environ.get("RC_RUN_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )
    try:
        from src.hooks import _plan_scaffold as _ps  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import _plan_scaffold as _ps  # type: ignore
        except Exception:  # noqa: BLE001
            return
    try:
        created = _ps.maybe_scaffold_plan(project_dir)
    except Exception:  # noqa: BLE001
        return
    if created is None:
        return
    # Emit a one-line audit receipt so operators can see the scaffold fired.
    try:
        _hooks_dir = os.path.dirname(os.path.abspath(__file__))
        if _hooks_dir not in sys.path:
            sys.path.insert(0, _hooks_dir)
        import audit_log  # type: ignore
        audit_log.append_event(audit_log.new_event(
            tool_name="SessionStart",
            decision="injected",
            file_path=str(created),
            signal_source="plan_scaffold",
            reason="auto_scaffolded_from_readme",
            gate_id="plan_grounding",
        ))
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    env_value = os.environ.get("RC_BEST_EFFORT_SPEC", "")
    fired = env_value == "1"
    if fired:
        _emit_envelope()
    _emit_receipt(fired=fired, env_value=env_value)
    try:
        _maybe_scaffold_plan()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
