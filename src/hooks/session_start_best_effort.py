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

import json
import os
import sys

# Iter-3 single-factor: license-removal sentence ONLY. The previous draft
# trailed "— always pair it with the closest compilable artifact the contract
# permits", which prescribes the substitution recipe and would confound the
# iter-4 ablation between license-removal-alone vs full-recipe variants. Per
# scientist review v3, the recipe MUST live in a separate env-var (e.g.
# RC_BEST_EFFORT_RECIPE=1) when iter-4 ships; this v3 string is the minimal arm.
_OVERLAY = (
    "Never ship a DIVERGENCES.md alone."
)


def _emit() -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _OVERLAY,
        }
    }
    sys.stdout.write(json.dumps(out))


def main() -> int:
    if os.environ.get("RC_BEST_EFFORT_SPEC") != "1":
        return 0
    _emit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
