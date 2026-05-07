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

# Iter-3 single-factor: license-removal sentence ONLY. The previous draft
# trailed "— always pair it with the closest compilable artifact the contract
# permits", which prescribes the substitution recipe and would confound the
# iter-4 ablation between license-removal-alone vs full-recipe variants. Per
# scientist review v3, the recipe MUST live in a separate env-var (e.g.
# RC_BEST_EFFORT_RECIPE=1) when iter-4 ships; this v3 string is the minimal arm.
# Overlay v2 (sweep round-6 #3): no-op carve-out added.
# Reviewer-flagged risk: on a "no production change needed" task (T8-style
# cutover where the rename was already applied or no rename is required),
# the v1 overlay "Never ship a DIVERGENCES.md alone" pressures the agent
# to invent edits to satisfy the rule, harming impl_quality. The carve-out
# preserves the license-removal intent while explicitly authorizing the
# correct response when no edit is appropriate. Single-factor for iter-4
# attribution: the no-op exception is the same construct (license shape),
# not a substitution-recipe expansion (still deferred to iter-4 under
# a separate env var).
_OVERLAY = (
    "Never ship a DIVERGENCES.md alone — unless the contract requires no "
    "production change (e.g. rename already applied upstream, no behavioral "
    "change required), in which case ship a one-line PLAN.md stating why "
    "no edit is appropriate and produce no other artifact."
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


def main() -> int:
    env_value = os.environ.get("RC_BEST_EFFORT_SPEC", "")
    fired = env_value == "1"
    if fired:
        _emit_envelope()
    _emit_receipt(fired=fired, env_value=env_value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
