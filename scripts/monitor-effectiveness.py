#!/usr/bin/env python3
"""Effectiveness monitor for reasoning-core.

Reads the JSONL audit log at ``~/.local/share/reasoning-core/events/<YYYY-MM-DD>/``
and reports which community pains (per
``thoughts/shared/research/2026-06-02-community-pain-points.md``) are firing in
production. Run after a workday of normal use to see if the new gates
(``validate_imports``, ``RC_PROJECT_INDEX=1`` Phase-2 dims, decomposition
recipe) are actually addressing them.

Categories the monitor counts:

1. Scope creep        — gate_plan_grounding decisions
2. Pattern blindness  — novelty / project_fan_in dim signals + symbol_index hits
3. Spec drift         — plan_quality reject/warn + decomposition_recipe emit
4. Token waste        — hard_cap_exceeded events (sidecar timeouts)
5. Local-only         — non-loopback connection attempts (should be 0)
6. Hallucinated APIs  — validate_imports unresolved / forbid_import denies
7. CLAUDE.md ignored  — exit-2 hard blocks vs shadow-mode logs
8. Over-engineering   — RC_PLAN_LOC_BLOCK denies, phase-ratio warns
9. Repo conventions   — RC_LANG_LOCK + forbid_pattern denies

Output is a CSV-like table plus a verdict per pain (working / weak / silent).
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import sys
import time
from datetime import date, datetime, timedelta

EVENTS_DIR = pathlib.Path.home() / ".local/share/reasoning-core/events"


def _iter_events(days: int = 7):
    today = date.today()
    for n in range(days):
        d = today - timedelta(days=n)
        day_dir = EVENTS_DIR / d.isoformat()
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob("*.jsonl"):
            try:
                with f.open() as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue


PAIN_CATEGORIES = {
    "1_scope_creep": {
        "signal_sources": {"plan_grounding"},
        "reasons_substr": {"plan_grounding", "off_plan", "not_in_plan"},
    },
    "2_pattern_blindness": {
        "signal_sources": {"novelty", "session_centroid_drift",
                           "project_fan_in", "project_coupling"},
        "reasons_substr": {"novelty", "centroid_drift", "fan_in", "coupling"},
    },
    "3_spec_drift": {
        "signal_sources": {"plan_quality", "decomposition_recipe"},
        "reasons_substr": {"cgs_below", "plan_quality", "decomp",
                           "framework_pivot", "boundary_prose"},
    },
    "4_token_waste": {
        "signal_sources": {"hard_cap", "bm25_fallback"},
        "reasons_substr": {"hard_cap_exceeded", "timeout", "gen_timeout"},
    },
    "5_local_only_violations": {
        "signal_sources": {"network_exfil"},
        "reasons_substr": {"non_loopback", "external_nic"},
    },
    "6_hallucinated_apis": {
        "signal_sources": {"validate_imports", "rule_engine_import",
                           "forbid_import"},
        "reasons_substr": {"unknown_module", "unknown_symbol",
                           "forbid_import"},
    },
    "7_runtime_enforcement": {
        "signal_sources": set(),
        "decisions": {"blocked", "exit_block", "exit_blocked"},
    },
    "8_over_engineering": {
        "signal_sources": {"plan_loc", "phase_ratio"},
        "reasons_substr": {"loc_block", "loc_budget", "phase_to_file_ratio",
                           "per_file_loc"},
    },
    "9_repo_conventions": {
        "signal_sources": {"lang_lock", "rule_engine_pattern",
                           "forbid_pattern"},
        "reasons_substr": {"lang_lock", "forbid_pattern", "language_mismatch"},
    },
}


def classify(ev: dict) -> set[str]:
    """Return the set of pain categories this event signals."""
    hits: set[str] = set()
    src = (ev.get("signal_source") or "").lower()
    reason = (ev.get("reason") or "").lower()
    decision = (ev.get("decision") or "").lower()

    for cat, rules in PAIN_CATEGORIES.items():
        if src in rules.get("signal_sources", set()):
            hits.add(cat)
            continue
        for substr in rules.get("reasons_substr", set()):
            if substr in reason:
                hits.add(cat)
                break
        if decision in rules.get("decisions", set()):
            hits.add(cat)
    return hits


def main(argv: list[str]) -> int:
    days = int(argv[1]) if len(argv) > 1 else 7

    total = 0
    by_cat = collections.Counter()
    by_decision = collections.Counter()
    by_source = collections.Counter()
    by_reason = collections.Counter()
    fired_today = collections.Counter()
    today_iso = date.today().isoformat()

    for ev in _iter_events(days):
        total += 1
        decision = ev.get("decision", "")
        src = ev.get("signal_source", "")
        reason = ev.get("reason", "")
        by_decision[decision] += 1
        by_source[src] += 1
        if reason:
            by_reason[reason] += 1
        cats = classify(ev)
        for c in cats:
            by_cat[c] += 1
            ts = ev.get("ts", "")
            if isinstance(ts, str) and ts.startswith(today_iso):
                fired_today[c] += 1

    print(f"reasoning-core effectiveness monitor — last {days} day(s)")
    print(f"audit events read: {total}")
    print()
    print("=== by decision ===")
    for d, n in by_decision.most_common():
        print(f"  {d or '(none)':<30} {n}")
    print()
    print("=== by signal_source (top 15) ===")
    for s, n in by_source.most_common(15):
        print(f"  {s or '(none)':<30} {n}")
    print()
    print("=== pain categories firing ===")
    print(f"  {'category':<28} {'total':>7} {'today':>7}  verdict")
    for cat in PAIN_CATEGORIES:
        tot = by_cat.get(cat, 0)
        tod = fired_today.get(cat, 0)
        if tot == 0:
            verdict = "silent (no signal)"
        elif tot < 3:
            verdict = "weak (<3 fires)"
        elif tod == 0:
            verdict = "warm (stale)"
        else:
            verdict = "active"
        print(f"  {cat:<28} {tot:>7} {tod:>7}  {verdict}")
    print()
    print("=== top reasons (10) ===")
    for r, n in by_reason.most_common(10):
        print(f"  {n:>5}  {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
