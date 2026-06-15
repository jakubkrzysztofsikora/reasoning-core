"""Block message formatter with repair hints. Split from pre_edit_guard."""
from __future__ import annotations

HINTS = {
    "cyclomatic": "Extract helper functions to reduce branching. Target <= 10 branches per function.",
    "fan_in": "Reduce callers of this function by inlining small callers or adding an adapter layer.",
    "fan_out": "Extract dependencies into a dedicated module; this file calls too many external symbols.",
    "depth": "Flatten nested conditionals using early returns or guard clauses.",
    "churn": "Split this file into 2-3 smaller files, each with a single responsibility.",
    "coupling": "Move tightly-coupled logic into the same module; increase cohesion.",
    "cohesion": "Group related functions together; unrelated utilities belong in separate files.",
    "novelty": "Align with existing patterns in the repo; check similar files for reference.",
    "session_centroid_drift": "This file is drifting from its session baseline. Verify the change is intentional and not scope creep.",
    "project_fan_in": "This file has high inbound coupling. Changes here affect many callers -- add tests or split the interface.",
    "project_coupling": "This file couples unrelated modules. Introduce an abstraction to break the dependency.",
}

# Stderr deliberately does NOT enumerate override routes — naming them in
# agent-visible output trains the agent to attempt the bypass. Operators
# learn the override mechanisms from docs / `rc status`, not from blocks.
GUIDANCE = (
    "\n  Address the top risk contributors above before retrying.\n"
)

RETRY = "\n  RETRY DETECTED: same file was blocked recently. Revise content; do not retry the same write.\n"

DEGRADED_FALLBACK = (
    "\n  RECOVERY PATH (if retries keep failing):\n"
    "    1. Emit final patch as a fenced ```diff … ``` block.\n"
    "    2. Every hunk-body line MUST start with one of: ' ', '+', '-', '\\\\'.\n"
    "    3. Do NOT wrap long lines — each hunk-body line stays on ONE\n"
    "       physical line regardless of length.\n"
    "    4. Hunk header `@@ -A,B +C,D @@` MUST satisfy:\n"
    "         B == count(context) + count(removed)\n"
    "         D == count(context) + count(added)\n"
    "    5. Blank context lines are encoded as a single space, never empty.\n"
    "    6. Optional: call MCP tool `validate_unified_diff` before final\n"
    "       emission to catch structural errors.\n"
)


def _f(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "n/a"


def top3(report):
    rv = report.get("risk_vector") or []
    rl = report.get("risk_labels") or []
    if len(rv) != len(rl) or not rv:
        return []
    pairs = sorted(zip(rl, rv), key=lambda p: float(p[1] or 0), reverse=True)[:3]
    return [f"    - {l}={_f(v)}  -> {HINTS.get(l, '')}" for l, v in pairs]


def _fired_lines(report):
    fired = report.get("fired_conditions") or []
    margins = report.get("fired_margins") or {}
    if not fired:
        return ""
    lines = ["  fired conditions:"]
    for cond in fired:
        margin = margins.get(cond, 0)
        lines.append(f"    - {cond} (margin={margin:.2f})")
    return "\n" + "\n".join(lines)


def format_block(file_path, report, is_retry=False):
    contribs = "\n".join(top3(report)) or "    (none)"
    retry = RETRY if is_retry else ""
    cd_thr = report.get("cd_threshold")
    cd_thr_str = _f(cd_thr) if cd_thr is not None else "1.50"
    kind = report.get("file_kind") or "source_code"
    fired_lines = _fired_lines(report)
    return (
        "[hybrid-reasoner] BLOCKED: architectural regression detected\n"
        f"  file: {file_path}  ({kind})\n"
        f"  AIS: {_f(report.get('architectural_impact_score'))}  (thr 0.40)\n"
        f"  coherence_delta: {_f(report.get('coherence_delta'))}  (thr {cd_thr_str})\n"
        f"  top risk contributors:\n{contribs}{fired_lines}\n"
        f"  summary: {report.get('human_summary') or '(none)'}"
        f"{retry}{GUIDANCE}{DEGRADED_FALLBACK if is_retry else ''}"
    )
