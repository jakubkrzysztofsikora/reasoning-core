"""Block message formatter with repair hints. Split from pre_edit_guard."""
from __future__ import annotations

HINTS = {
    "cyclomatic": "split function or reduce branches",
    "fan_in": "narrow public surface",
    "fan_out": "extract a layer",
    "depth": "flatten nesting",
    "churn": "split into smaller files",
    "coupling": "tighten module boundaries",
    "cohesion": "group related code",
    "novelty": "align with existing patterns",
}

GUIDANCE = (
    "\n  This block is a signal to IMPROVE the file, not bypass it.\n"
    "  Address the top risk contributors above. Bypass via heredoc, sed,\n"
    "  or subagent will also be blocked.\n"
)

RETRY = "\n  RETRY DETECTED: same file blocked recently. Revise content, do not retry.\n"


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


def format_block(file_path, report, is_retry=False):
    contribs = "\n".join(top3(report)) or "    (none)"
    retry = RETRY if is_retry else ""
    cd_thr = report.get("cd_threshold")
    cd_thr_str = _f(cd_thr) if cd_thr is not None else "1.50"
    kind = report.get("file_kind") or "source_code"
    return (
        "[hybrid-reasoner] BLOCKED: architectural regression detected\n"
        f"  file: {file_path}  ({kind})\n"
        f"  AIS: {_f(report.get('architectural_impact_score'))}  (thr 0.40)\n"
        f"  coherence_delta: {_f(report.get('coherence_delta'))}  (thr {cd_thr_str})\n"
        f"  top risk contributors:\n{contribs}\n"
        f"  summary: {report.get('human_summary') or '(none)'}"
        f"{retry}{GUIDANCE}"
    )
