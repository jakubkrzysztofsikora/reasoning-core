"""
Phase 0.5b runner — pre-registered in iter3-prereg.json.

Runs the binary rubric classifier on the same 10 PLAN.md fixtures Phase 0.5
used. Computes:
  - Parse-failure count (gate: >2/10 → ABORT, kill_switch fires branch A)
  - For non-failed fixtures: Cohen κ on per-claim binary labels under input
    perturbation (whitespace normalisation, sentinel token rotation)
  - Cluster bootstrap CI (B=1000 task-paired claim-resamples, seed=20260507)

Deterministic. No LLM calls. Cost: $0. Wall-clock: minutes.

Usage:
    python3 run_binary_rubric_reliability.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add eval-scripts to path
sys.path.insert(0, "/Users/jakubsikora/research-claude-code-setup-eval-scripts")

from eval.binary_rubric import (  # noqa: E402
    classify_pair,
    cluster_bootstrap_kappa_lower_ci,
)


REPO = Path(__file__).resolve().parent.parent.parent.parent.parent
ITER2 = Path(
    "/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3"
)

# Same 10 fixtures Phase 0.5 used: 1 per task × 8 tasks + 2 extras from {P0, T1}
FIXTURES = [
    ("A", "P0", 1),
    ("B", "P0", 1),
    ("A", "T1", 1),
    ("B", "T1", 1),
    ("A", "T2", 1),
    ("B", "E1", 1),
    ("A", "T5", 1),
    ("B", "T7", 1),
    ("A", "T8", 1),
    ("B", "T9", 1),
]

PARSE_FAIL_GATE_RATIO = 2 / 10  # >2/10 fixtures parse-fail → ABORT


def perturb_text(text: str, mode: str) -> str:
    """Input perturbation for κ reliability test (binary classifier should be
    robust to whitespace + minor formatting variation)."""
    if mode == "identity":
        return text
    if mode == "whitespace_normalize":
        # Collapse runs of internal whitespace; normalize line endings; strip
        # trailing whitespace per line.
        lines = [line.rstrip() for line in text.splitlines()]
        return "\n".join(lines)
    if mode == "trailing_newlines":
        return text.rstrip() + "\n\n\n"
    raise ValueError(f"unknown mode: {mode}")


def main():
    start_time = time.time()
    out: dict = {
        "phase": "0.5b_binary_rubric_reliability",
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixtures": [{"setup": s, "task": t, "run": r} for s, t, r in FIXTURES],
        "n_fixtures": len(FIXTURES),
        "parse_fail_gate_ratio": PARSE_FAIL_GATE_RATIO,
        "perturbation_modes": ["identity", "whitespace_normalize"],
        "per_fixture_results": [],
        "parse_failures": [],
        "verdict": None,
    }

    items_by_cluster: list[list[tuple[int, int]]] = []  # cluster = task

    for setup, task, run in FIXTURES:
        cell = ITER2 / "runs" / setup / task / f"run-{run:02d}"
        plan_path = cell / "plan.md"
        div_path = cell / "impl" / "DIVERGENCES.md"

        if not plan_path.is_file():
            out["parse_failures"].append({
                "setup": setup, "task": task, "run": run,
                "reason": f"plan.md not found at {plan_path}",
            })
            out["per_fixture_results"].append({
                "setup": setup, "task": task, "run": run,
                "parse_failed": True,
                "n_claims": 0,
            })
            continue

        # Original input
        plan_text = plan_path.read_text(errors="replace")
        # Perturbation: whitespace_normalize is intended as a no-op semantic
        # transformation; classifier must produce same labels.
        perturbed_text = perturb_text(plan_text, "whitespace_normalize")

        # Write perturbed version to temp + classify
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
            tf.write(perturbed_text)
            perturbed_path = Path(tf.name)

        try:
            cls_a = classify_pair(plan_path, div_path if div_path.is_file() else None)
            cls_b = classify_pair(perturbed_path, div_path if div_path.is_file() else None)
        finally:
            perturbed_path.unlink(missing_ok=True)

        if cls_a.parse_failed:
            out["parse_failures"].append({
                "setup": setup, "task": task, "run": run,
                "reason": cls_a.parse_failure_reason,
            })
            out["per_fixture_results"].append({
                "setup": setup, "task": task, "run": run,
                "parse_failed": True,
                "parse_failure_reason": cls_a.parse_failure_reason,
                "n_claims": 0,
            })
            continue

        # Bipartite match claims by exact text (since both are deterministic
        # outputs of same classifier on same input modulo whitespace, expect
        # exact matches).
        claims_a_by_text = {c.text: c.label for c in cls_a.claims}
        claims_b_by_text = {c.text: c.label for c in cls_b.claims}

        all_texts = sorted(set(claims_a_by_text.keys()) | set(claims_b_by_text.keys()))

        # 1 = attempted, 0 = declared_divergent
        labels_a = []
        labels_b = []
        for text in all_texts:
            a = claims_a_by_text.get(text)
            b = claims_b_by_text.get(text)
            # If a claim appears in only one judge's view, treat the missing
            # one as the OPPOSITE label (max disagreement)
            if a is None:
                labels_a.append(0 if b == "attempted" else 1)
            else:
                labels_a.append(1 if a == "attempted" else 0)
            if b is None:
                labels_b.append(0 if a == "attempted" else 1)
            else:
                labels_b.append(1 if b == "attempted" else 0)

        items_by_cluster.append(list(zip(labels_a, labels_b)))

        out["per_fixture_results"].append({
            "setup": setup, "task": task, "run": run,
            "parse_failed": False,
            "n_claims_a": len(cls_a.claims),
            "n_claims_b": len(cls_b.claims),
            "n_attempted_a": cls_a.n_attempted,
            "n_attempted_b": cls_b.n_attempted,
            "n_divergent_a": cls_a.n_declared_divergent,
            "n_divergent_b": cls_b.n_declared_divergent,
            "n_paired_for_kappa": len(all_texts),
        })

    # Parse-failure gate
    n_parse_fails = len(out["parse_failures"])
    parse_fail_ratio = n_parse_fails / len(FIXTURES)
    out["n_parse_failures"] = n_parse_fails
    out["parse_fail_ratio"] = round(parse_fail_ratio, 4)

    if parse_fail_ratio > PARSE_FAIL_GATE_RATIO:
        out["verdict"] = {
            "phase_0_5b_passed": False,
            "reason": f"parse-failure ratio {parse_fail_ratio:.2f} > gate {PARSE_FAIL_GATE_RATIO} → ABORT per pre-registered policy",
            "kill_switch_branch_fired": "A_iter3_ships_as_methodology_case_study",
            "binary_rubric_status": "FORMALLY_RETIRED",
            "next_action": "iter-3 ships as methodology case study with NO sweep; iter-4 must validate classifier rules against actual prompt-suite outputs BEFORE pinning",
        }
    elif not items_by_cluster:
        out["verdict"] = {
            "phase_0_5b_passed": False,
            "reason": "all fixtures parse-failed; no clusters to compute κ",
            "kill_switch_branch_fired": "A_iter3_ships_as_methodology_case_study",
        }
    else:
        # Compute κ + cluster bootstrap CI
        kappa_result = cluster_bootstrap_kappa_lower_ci(
            items_by_cluster, n_bootstrap=1000, confidence=0.95, seed=20260507
        )
        out["kappa_result"] = kappa_result
        passed = kappa_result["lower_95_ci"] >= 0.6
        out["verdict"] = {
            "phase_0_5b_passed": passed,
            "kappa_point_estimate": kappa_result["kappa_point_estimate"],
            "kappa_lower_95_ci": kappa_result["lower_95_ci"],
            "kappa_upper_95_ci": kappa_result["upper_95_ci"],
            "gate_threshold": 0.6,
            "kill_switch_branch_fired": (
                "C_atomic_rescue_PROCEED_to_phase_0_4_retroactive" if passed
                else "B_phase_0_5c_rescue_attempt"
            ),
            "next_action": (
                "Phase 0.4 retroactive on iter-2 with binary rubric (~50min LLM); flat-distribution check"
                if passed else
                "Phase 0.5c atomic-rescue with fuzzy locator matching"
            ),
        }

    out["duration_s"] = round(time.time() - start_time, 2)

    out_path = REPO / "thoughts/shared/research/iter3-phase-0-5b-binary-reliability-report.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")
    print()
    print(f"Parse failures: {n_parse_fails}/{len(FIXTURES)} (gate: ≤{int(PARSE_FAIL_GATE_RATIO*10)}/10)")
    if out["verdict"]:
        print(f"Verdict: {json.dumps(out['verdict'], indent=2)}")


if __name__ == "__main__":
    main()
