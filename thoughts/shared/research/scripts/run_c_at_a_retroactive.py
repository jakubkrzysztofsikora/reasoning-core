"""Phase 0.4 — retroactive c_at_a_min on iter-2 frozen.

Decompose every (PLAN.md, DIVERGENCES.md) pair across all 48 iter-2 cells
using Gemini (cost minimisation; Vibe used in Phase 0.5 reliability), compute
per-cell c_at_a_min = min(coverage, accuracy), and emit the distribution.

  coverage = max(0, plan_claims - declared_divergent) / max(1, plan_claims)
  accuracy = locked_pass_rate from runs/<setup>/<task>/run-NN/tests/locked.jsonl
  c_at_a_min = min(coverage, accuracy)

Pre-registered abort condition (iter3-prereg.json:c_at_a_combinator.abort_condition):
  if all c_at_a_min values within ±0.05 of each other → metric has no signal,
  abort iter-3 plan and revisit Phase 2.1.

Output: thoughts/shared/research/iter3-c-at-a-retroactive-iter2.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

EVAL_REPO = Path("/Users/jakubsikora/research-claude-code-setup-eval-scripts")
if str(EVAL_REPO) not in sys.path:
    sys.path.insert(0, str(EVAL_REPO))

from eval.decomposer import decompose_plan  # noqa: E402

ITER2_ROOT = Path("/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3")
PROMPT_MD = Path(
    "/Users/jakubsikora/Repos/personal/reasoning-core/thoughts/shared/research/iter3-prereg-decomposer-prompt.md"
)
OUT_PATH = Path(
    "/Users/jakubsikora/Repos/personal/reasoning-core/thoughts/shared/research/iter3-c-at-a-retroactive-iter2.json"
)
TBD_MAX_PCT = 30.0
ABORT_FLAT_DELTA = 0.05


def locked_pass_rate(run_dir: Path) -> float | None:
    locked = run_dir / "tests" / "locked.jsonl"
    if not locked.is_file():
        return None
    rows = [json.loads(l) for l in locked.read_text().splitlines() if l.strip()]
    if not rows:
        return None
    passed = sum(1 for r in rows if r.get("exit_code") == 0)
    return passed / len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", default="gemini",
                    help="decomposer judge — Gemini only by default to control cost")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--limit", type=int, default=0,
                    help="if >0, decompose only N cells (smoke test)")
    ap.add_argument("--runs", default="all",
                    help="comma-separated run indices to keep (e.g. '1' or '1,2'); "
                         "'all' keeps every run found")
    args = ap.parse_args(argv)

    runs_root = ITER2_ROOT / "runs"
    cells: list[tuple[str, str, int, Path]] = []
    for setup_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in setup_dir.iterdir() if p.is_dir()):
            for run_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
                if not run_dir.name.startswith("run-"):
                    continue
                run_n = int(run_dir.name.split("-")[1])
                cells.append((setup_dir.name, task_dir.name, run_n, run_dir))

    if args.runs != "all":
        keep_runs = {int(r) for r in args.runs.split(",") if r.strip()}
        cells = [c for c in cells if c[2] in keep_runs]
    if args.limit > 0:
        cells = cells[: args.limit]

    started = time.time()
    per_cell: list[dict] = []
    errors: list[dict] = []
    # Incremental progress file — survives mid-run kill so partial decompositions
    # are not wasted.
    progress_path = Path(args.out + ".progress")
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    for i, (setup, task, run_n, run_dir) in enumerate(cells):
        plan_path = run_dir / "plan.md"
        div_path = run_dir / "impl" / "DIVERGENCES.md"
        cell_id = f"{setup}/{task}/run-{run_n:02d}"
        record: dict = {
            "setup": setup, "task": task, "run": run_n,
            "plan_path": str(plan_path), "div_path": str(div_path),
        }
        try:
            t0 = time.time()
            d_plan = decompose_plan(plan_path, args.judge, PROMPT_MD,
                                    timeout_seconds=args.timeout)
            d_div = decompose_plan(div_path, args.judge, PROMPT_MD,
                                   timeout_seconds=args.timeout) \
                if div_path.is_file() else None
            n_plan = d_plan.n_claims
            n_div = d_div.n_claims if d_div else 0
            tbd_plan = d_plan.tbd_pct
            tbd_div = d_div.tbd_pct if d_div else 0.0
            tbd_breach = (tbd_plan > TBD_MAX_PCT) or (tbd_div > TBD_MAX_PCT)
            coverage = max(0, n_plan - n_div) / n_plan if n_plan else 0.0
            accuracy = locked_pass_rate(run_dir)
            if accuracy is None:
                record["error"] = "missing_locked_jsonl"
                errors.append({"cell": cell_id, "error": record["error"]})
            c_at_a_min = (
                min(coverage, accuracy) if accuracy is not None else None
            )
            record.update({
                "n_plan_claims": n_plan,
                "n_divergent_claims": n_div,
                "coverage": coverage,
                "accuracy_locked_pass_rate": accuracy,
                "c_at_a_min": c_at_a_min,
                "tbd_pct_plan": tbd_plan,
                "tbd_pct_div": tbd_div,
                "tbd_breach": tbd_breach,
                "duration_s": time.time() - t0,
            })
            print(f"[{i+1}/{len(cells)}] {cell_id} "
                  f"plan={n_plan} div={n_div} cov={coverage:.2f} "
                  f"acc={accuracy} c_at_a_min={c_at_a_min} "
                  f"({record['duration_s']:.1f}s)",
                  flush=True)
        except Exception as e:
            record["error"] = str(e)
            errors.append({"cell": cell_id, "error": str(e)})
            print(f"[{i+1}/{len(cells)}] [ERR] {cell_id}: {e}",
                  file=sys.stderr, flush=True)
        per_cell.append(record)
        # Append-only progress log (one JSON object per line) so a kill
        # mid-run preserves what we have so far.
        with progress_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    valid = [r["c_at_a_min"] for r in per_cell
             if isinstance(r.get("c_at_a_min"), (int, float))]
    summary: dict = {
        "n_cells_total": len(cells),
        "n_cells_with_c_at_a": len(valid),
        "n_errors": len(errors),
        "n_tbd_breach_cells": sum(1 for r in per_cell if r.get("tbd_breach")),
    }
    if valid:
        v_min, v_max = min(valid), max(valid)
        summary.update({
            "min": v_min,
            "max": v_max,
            "range": v_max - v_min,
            "mean": statistics.mean(valid),
            "median": statistics.median(valid),
            "stdev": statistics.pstdev(valid) if len(valid) > 1 else 0.0,
            "p25": statistics.quantiles(valid, n=4)[0] if len(valid) >= 4 else None,
            "p75": statistics.quantiles(valid, n=4)[2] if len(valid) >= 4 else None,
        })
        flat = (v_max - v_min) <= ABORT_FLAT_DELTA
    else:
        flat = True

    abort_signal = flat
    overall_pass = (len(valid) > 0) and (not flat)

    report = {
        "phase": "0.4_c_at_a_retroactive_sanity",
        "judge": args.judge,
        "iter2_root": str(ITER2_ROOT),
        "abort_flat_delta_threshold": ABORT_FLAT_DELTA,
        "summary": summary,
        "abort_signal_metric_is_flat": abort_signal,
        "verdict_pass": overall_pass,
        "per_cell": per_cell,
        "errors": errors,
        "duration_s": time.time() - started,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps({"summary": summary,
                      "abort_signal_metric_is_flat": abort_signal,
                      "verdict_pass": overall_pass,
                      "n_errors": len(errors)},
                     indent=2))
    print(f"wrote {out_path}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
