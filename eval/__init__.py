"""reasoning-core evaluation toolkit.

Public modules:

    eval.metrics    -- AST edit distance, cyclomatic delta, fan-in/out delta.
    eval.stats      -- paired Wilcoxon, BCa bootstrap, Holm-Bonferroni.
    eval.aggregate  -- joins per-task results, renders report.{md,json}.
    eval.run_suite  -- orchestrator that schedules run_task.sh per (task, arm).

The toolkit is import-safe even when scipy / statsmodels are absent; both
modules expose stdlib-only fallbacks. This keeps the hook path lean and the
test suite runnable in minimal environments.
"""

from __future__ import annotations

__all__ = [
    "metrics",
    "stats",
]
