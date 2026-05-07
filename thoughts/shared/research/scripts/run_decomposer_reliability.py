"""Phase 0.5 — decomposer reliability test.

Runs the iter-3 atomic-claim decomposer (Gemini + Vibe; Qwen-Coder deferred to
Phase 1.1) against 10 representative iter-2 PLAN.md files and reports
pairwise Jaccard + Pearson per the prereg's reliability protocol.

Pre-registered thresholds:
  decomposer_jaccard_floor = 0.6
  decomposer_pearson_count_smoke_test_floor = 0.7
  decomposer_tbd_evidence_locator_max_pct = 30.0

Output: thoughts/shared/research/iter3-decomposer-reliability-report.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from a checkout that doesn't import eval/* package-style.
EVAL_REPO = Path("/Users/jakubsikora/research-claude-code-setup-eval-scripts")
if str(EVAL_REPO) not in sys.path:
    sys.path.insert(0, str(EVAL_REPO))

from eval.decomposer import (  # noqa: E402
    compute_pairwise_jaccard,
    compute_pearson_claim_count,
    decompose_plan,
)


# 10 representative iter-2 PLAN.md files — 1 per task × 8 tasks + 2 extras
# from the discriminating tasks {P0, T1} where Setup B failed.
ITER2_ROOT = Path("/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3")
FIXTURE_PLANS = [
    ITER2_ROOT / "runs/A/P0/run-01/plan.md",
    ITER2_ROOT / "runs/B/P0/run-02/plan.md",   # extra (discriminating task)
    ITER2_ROOT / "runs/A/T1/run-01/plan.md",
    ITER2_ROOT / "runs/B/T1/run-01/plan.md",   # extra (discriminating task)
    ITER2_ROOT / "runs/A/T2/run-02/plan.md",
    ITER2_ROOT / "runs/B/E1/run-01/plan.md",
    ITER2_ROOT / "runs/A/T5/run-01/plan.md",
    ITER2_ROOT / "runs/B/T7/run-02/plan.md",
    ITER2_ROOT / "runs/A/T8/run-01/plan.md",
    ITER2_ROOT / "runs/B/T9/run-03/plan.md",
]
PROMPT_MD = Path(
    "/Users/jakubsikora/Repos/personal/reasoning-core/thoughts/shared/research/iter3-prereg-decomposer-prompt.md"
)
OUT_PATH = Path(
    "/Users/jakubsikora/Repos/personal/reasoning-core/thoughts/shared/research/iter3-decomposer-reliability-report.json"
)
JACCARD_FLOOR = 0.6
PEARSON_FLOOR = 0.7
TBD_MAX_PCT = 30.0
JUDGES = ("gemini", "vibe")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args(argv)

    out_path = Path(args.out)

    for p in FIXTURE_PLANS:
        if not p.is_file():
            print(f"FATAL: fixture plan missing: {p}", file=sys.stderr)
            return 2

    decomps: dict[str, list[dict]] = {j: [] for j in JUDGES}
    errors: list[dict] = []
    started = time.time()

    for plan_path in FIXTURE_PLANS:
        for judge in JUDGES:
            t0 = time.time()
            try:
                d = decompose_plan(plan_path, judge, PROMPT_MD, timeout_seconds=args.timeout)
                decomps[judge].append(d.to_dict())
                print(f"[ok] {judge:6s} {plan_path.relative_to(ITER2_ROOT)} "
                      f"n={d.n_claims} tbd={d.tbd_pct:.1f}% ({time.time()-t0:.1f}s)",
                      flush=True)
            except Exception as e:
                err = {"plan": str(plan_path), "judge": judge, "error": str(e)}
                errors.append(err)
                decomps[judge].append({
                    "plan_path": str(plan_path), "judge": judge,
                    "n_claims": 0, "n_tbd": 0, "tbd_pct": 0.0, "claims": [],
                    "error": str(e),
                })
                print(f"[ERR] {judge:6s} {plan_path.relative_to(ITER2_ROOT)} :: {e}",
                      file=sys.stderr, flush=True)

    # Per-document Jaccard (Gemini vs Vibe)
    per_doc = []
    pearson_counts_g, pearson_counts_v = [], []
    for i, plan_path in enumerate(FIXTURE_PLANS):
        dg = decomps["gemini"][i]
        dv = decomps["vibe"][i]
        jacc = compute_pairwise_jaccard(dg.get("claims", []), dv.get("claims", []))
        per_doc.append({
            "plan_path": str(plan_path),
            "n_claims_gemini": dg.get("n_claims", 0),
            "n_claims_vibe": dv.get("n_claims", 0),
            "tbd_pct_gemini": dg.get("tbd_pct", 0.0),
            "tbd_pct_vibe": dv.get("tbd_pct", 0.0),
            "tbd_breach_gemini": dg.get("tbd_pct", 0.0) > TBD_MAX_PCT,
            "tbd_breach_vibe": dv.get("tbd_pct", 0.0) > TBD_MAX_PCT,
            "jaccard_g_v": jacc,
        })
        pearson_counts_g.append(dg.get("n_claims", 0))
        pearson_counts_v.append(dv.get("n_claims", 0))

    jaccards = [d["jaccard_g_v"] for d in per_doc]
    mean_jacc = sum(jaccards) / len(jaccards) if jaccards else 0.0
    median_jacc = sorted(jaccards)[len(jaccards)//2] if jaccards else 0.0
    pearson = compute_pearson_claim_count(pearson_counts_g, pearson_counts_v)

    jacc_pass = mean_jacc >= JACCARD_FLOOR
    pearson_pass = (pearson == pearson) and (pearson >= PEARSON_FLOOR)  # NaN check
    overall_pass = jacc_pass and pearson_pass and not errors

    n_tbd_breaches = sum(1 for d in per_doc if d["tbd_breach_gemini"] or d["tbd_breach_vibe"])

    report = {
        "phase": "0.5_decomposer_reliability",
        "judges": list(JUDGES),
        "qwen_coder_status": "DEFERRED_TO_PHASE_1_1_per_blocker_5",
        "n_fixtures": len(FIXTURE_PLANS),
        "fixture_plans": [str(p) for p in FIXTURE_PLANS],
        "thresholds": {
            "jaccard_floor": JACCARD_FLOOR,
            "pearson_count_smoke_floor": PEARSON_FLOOR,
            "tbd_max_pct": TBD_MAX_PCT,
        },
        "results": {
            "mean_jaccard_g_v": mean_jacc,
            "median_jaccard_g_v": median_jacc,
            "min_jaccard_g_v": min(jaccards) if jaccards else 0.0,
            "max_jaccard_g_v": max(jaccards) if jaccards else 0.0,
            "pearson_count_g_v": pearson,
            "n_tbd_breach_documents": n_tbd_breaches,
        },
        "per_document": per_doc,
        "errors": errors,
        "verdicts": {
            "jaccard_pass": jacc_pass,
            "pearson_pass": pearson_pass,
            "overall_pass": overall_pass,
        },
        "duration_s": time.time() - started,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["results"] | report["verdicts"], indent=2))
    print(f"wrote {out_path}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
