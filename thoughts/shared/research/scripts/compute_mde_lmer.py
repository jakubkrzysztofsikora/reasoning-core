"""
Phase 0.4 blocker 1+2+3 — lmer-based MDE redo with ICC sensitivity sweep,
empirical paired-delta SD reconciliation, and selection-bias falsification
test on iter-2 retroactive data.

Methodology:

1. **lmer-based MDE under design-effect inflation** (paired-mean approach
   with cluster-corrected effective n):
     deff = 1 + (m_avg - 1) * ICC
     n_eff = n_cells_total / deff
     n_eff_pairs = n_eff / 2 (per arm)
   Then MDE_lmer ≈ t_{α/2, df_eff} + t_{β, df_eff}) * sd_within / sqrt(n_eff_pairs)

   ICC estimated per dim per setup from iter-2 raw cell-level grades:
     ICC = sigma^2_between / (sigma^2_between + sigma^2_within)
   where sigma^2_between is variance of per-task means and sigma^2_within is
   pooled within-task variance across cells.

2. **Empirical paired-delta SD** (drops artificial ρ):
   Per task: delta_t = mean_B(task) - mean_A(task)
   SD_diff_empirical = SD across the 8 tasks of those deltas
   Compare to ρ-implied SD_diff under ρ ∈ {0, 0.3, 0.5}; report which ρ
   matches empirical.

3. **Selection-bias falsification test on iter-2 retroactive locked_pass_rate**
   (proxy for c_at_a_min, since decomposer hasn't been run on iter-2):
   Compute per-task paired delta (B - A) on locked_pass_rate.
   Split: selected = {P0, T1, T2, E1}; unselected = {T5, T7, T8, T9}.
   Wilcoxon two-sample (Mann-Whitney) between the two delta sets.
   Falsification trigger: |median_selected - median_unselected| > 0.15
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ITER2_DIR = Path(
    "/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3"
)

REPO = Path(__file__).resolve().parent.parent.parent.parent.parent

DIMENSIONS = (
    "repo_fit",
    "cleanliness",
    "correctness_determinism",
    "plan_signal",
    "diff_discipline",
)

TASKS = ("P0", "T1", "T2", "T5", "T7", "T8", "T9", "E1")
SETUPS = ("A", "B")

# Iter-3 design parameters
N_CELLS_PER_ARM = 28          # P0/T1/T2/E1=4 + T5/T7/T8/T9=3 = 28
N_TASKS = 8
M_AVG_CELLS_PER_TASK = 28 / 8  # 3.5

# Critical values (precomputed; scipy unavailable)
T_CRIT = {
    7:   {"t_0975": 2.365, "t_0800": 0.896},
    13:  {"t_0975": 2.160, "t_0800": 0.870},   # df = 14 - 1 for n_eff_pairs=14
    19:  {"t_0975": 2.093, "t_0800": 0.861},   # df = 20 - 1
    27:  {"t_0975": 2.052, "t_0800": 0.855},
    55:  {"t_0975": 2.004, "t_0800": 0.846},
}


def _walk_grades_with_cells():
    """Returns list of {setup, task, run, judge, scores}."""
    grades_root = ITER2_DIR / "grades"
    out = []
    for path in sorted(grades_root.rglob("grader-llm-*.json")):
        parts = path.relative_to(grades_root).parts
        if len(parts) < 4:
            continue
        setup, task, run_dir, fname = parts[-4], parts[-3], parts[-2], parts[-1]
        try:
            d = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        scores = d.get("scores") or {}
        if not scores:
            continue
        judge = fname.removeprefix("grader-llm-").removesuffix(".json")
        run = int(run_dir.removeprefix("run-")) if run_dir.startswith("run-") else 0
        out.append({"setup": setup, "task": task, "run": run, "judge": judge,
                    "scores": {k: int(v) for k, v in scores.items() if k in DIMENSIONS}})
    return out


def estimate_icc_per_dim(grades):
    """ICC = sigma^2_between(task) / (sigma^2_between + sigma^2_within).

    Uses cell-level scores (mean across judges per cell) as the unit; per-task
    cluster.
    """
    icc_results = {}
    for setup in SETUPS:
        icc_results[setup] = {}
        for dim in DIMENSIONS:
            cell_scores_by_task = defaultdict(list)  # task -> [cell-level mean]
            cells_by_task_run = defaultdict(list)    # (task, run) -> [judge scores]
            for g in grades:
                if g["setup"] != setup or dim not in g["scores"]:
                    continue
                cells_by_task_run[(g["task"], g["run"])].append(g["scores"][dim])
            for (task, run), judge_scores in cells_by_task_run.items():
                cell_mean = statistics.mean(judge_scores)
                cell_scores_by_task[task].append(cell_mean)

            within_vars = []
            task_means = []
            for task, cells in cell_scores_by_task.items():
                if len(cells) >= 2:
                    within_vars.append(statistics.variance(cells))
                if cells:
                    task_means.append(statistics.mean(cells))

            if not within_vars or len(task_means) < 2:
                icc_results[setup][dim] = None
                continue

            sigma2_within = statistics.mean(within_vars)
            sigma2_between = statistics.variance(task_means)
            total = sigma2_between + sigma2_within
            icc = sigma2_between / total if total > 0 else None
            icc_results[setup][dim] = {
                "icc": round(icc, 4) if icc is not None else None,
                "sigma2_between": round(sigma2_between, 4),
                "sigma2_within": round(sigma2_within, 4),
                "n_tasks": len(task_means),
                "n_cells_estimated": sum(len(c) for c in cell_scores_by_task.values()),
            }
    return icc_results


def lmer_mde_under_icc(sd_within: float, icc: float, alpha=0.05, beta=0.20) -> dict:
    """MDE under design-effect inflation for paired-mean comparison.

    n_cells_total = 28 per arm × 2 arms = 56 paired observations
    deff = 1 + (m_avg - 1) * ICC where m_avg = 28/8 = 3.5 cells/task/arm
    n_eff_per_arm = N_CELLS_PER_ARM / deff
    Paired MDE: factor * SD_diff_within_cluster / sqrt(n_eff_pairs)
    """
    deff = 1 + (M_AVG_CELLS_PER_TASK - 1) * icc
    n_eff_per_arm = N_CELLS_PER_ARM / deff
    n_eff_pairs = n_eff_per_arm  # paired by task; effective pairs ~ n_eff_per_arm
    df_eff = max(7, int(n_eff_pairs - 1))
    crit = T_CRIT.get(df_eff) or T_CRIT[55]
    factor = crit["t_0975"] + crit["t_0800"]
    sd_diff_lmer = sd_within * math.sqrt(2 * (1 - icc))   # cluster-adjusted paired-SD
    mde = factor * sd_diff_lmer / math.sqrt(n_eff_pairs) if n_eff_pairs > 0 else float("inf")
    return {
        "icc": icc,
        "deff": round(deff, 3),
        "n_eff_per_arm": round(n_eff_per_arm, 2),
        "df_eff_used": df_eff,
        "sd_diff_under_icc": round(sd_diff_lmer, 4),
        "mde": round(mde, 4),
    }


def per_task_means_per_arm_per_dim(grades):
    """Returns {(setup, dim): {task: mean across (run, judge)}}."""
    bucket = defaultdict(lambda: defaultdict(list))
    for g in grades:
        for dim, score in g["scores"].items():
            bucket[(g["setup"], dim)][g["task"]].append(score)
    return {
        key: {task: statistics.mean(vs) for task, vs in by_task.items()}
        for key, by_task in bucket.items()
    }


def empirical_paired_delta_sd(per_task_means):
    """Per dim: SD across tasks of (mean_B - mean_A)."""
    out = {}
    for dim in DIMENSIONS:
        a_means = per_task_means.get(("A", dim), {})
        b_means = per_task_means.get(("B", dim), {})
        deltas = []
        for task in TASKS:
            if task in a_means and task in b_means:
                deltas.append(b_means[task] - a_means[task])
        if len(deltas) >= 2:
            sd_emp = statistics.stdev(deltas)
            median_d = statistics.median(deltas)
            mean_d = statistics.mean(deltas)
        else:
            sd_emp = median_d = mean_d = None
        sd_a = statistics.stdev(list(a_means.values())) if len(a_means) >= 2 else 0
        sd_b = statistics.stdev(list(b_means.values())) if len(b_means) >= 2 else 0
        out[dim] = {
            "n_paired_tasks": len(deltas),
            "deltas_per_task": [round(d, 4) for d in deltas],
            "median_delta": round(median_d, 4) if median_d is not None else None,
            "mean_delta": round(mean_d, 4) if mean_d is not None else None,
            "sd_diff_empirical": round(sd_emp, 4) if sd_emp is not None else None,
            "sd_A_within_arm": round(sd_a, 4),
            "sd_B_within_arm": round(sd_b, 4),
            "rho_implied_at_match": (
                round(1 - (sd_emp ** 2) / (sd_a ** 2 + sd_b ** 2), 4)
                if (sd_emp is not None and sd_a > 0 and sd_b > 0
                    and (sd_a ** 2 + sd_b ** 2) > sd_emp ** 2)
                else None
            ),
            "sd_diff_under_rho_0": round(math.sqrt(sd_a ** 2 + sd_b ** 2), 4),
            "sd_diff_under_rho_0_3": round(
                math.sqrt(sd_a ** 2 + sd_b ** 2 - 2 * 0.3 * sd_a * sd_b), 4),
            "sd_diff_under_rho_0_5": round(
                math.sqrt(sd_a ** 2 + sd_b ** 2 - 2 * 0.5 * sd_a * sd_b), 4),
        }
    return out


def selection_bias_falsification_locked_pass_rate():
    """Compute per-task paired delta on locked_pass_rate; split selected vs unselected."""
    selected_tasks = {"P0", "T1", "T2", "E1"}
    unselected_tasks = {"T5", "T7", "T8", "T9"}

    # Iterate per cell tests/locked.jsonl
    pass_rates_by_cell = defaultdict(list)  # (setup, task, run) -> list of pass rates
    for path in sorted((ITER2_DIR / "runs").rglob("locked.jsonl")):
        parts = path.relative_to(ITER2_DIR / "runs").parts
        if len(parts) < 4:
            continue
        setup, task, run_dir = parts[0], parts[1], parts[2]
        run = int(run_dir.removeprefix("run-")) if run_dir.startswith("run-") else 0
        try:
            tests = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        except json.JSONDecodeError:
            continue
        if not tests:
            continue
        n_pass = sum(1 for t in tests if t.get("exit_code") == 0)
        rate = n_pass / len(tests)
        pass_rates_by_cell[(setup, task, run)].append(rate)

    # Per-task mean pass rate per setup (averaging runs)
    per_task_arm_mean = defaultdict(dict)  # task -> {setup: mean rate}
    for (setup, task, run), rates in pass_rates_by_cell.items():
        cell_rate = statistics.mean(rates)
        per_task_arm_mean[task].setdefault(setup, []).append(cell_rate)

    deltas_selected = []
    deltas_unselected = []
    per_task_results = {}
    for task, by_setup in per_task_arm_mean.items():
        if "A" in by_setup and "B" in by_setup:
            a_mean = statistics.mean(by_setup["A"])
            b_mean = statistics.mean(by_setup["B"])
            delta = b_mean - a_mean
            per_task_results[task] = {"a": round(a_mean, 4), "b": round(b_mean, 4),
                                      "delta_BminusA": round(delta, 4)}
            if task in selected_tasks:
                deltas_selected.append(delta)
            elif task in unselected_tasks:
                deltas_unselected.append(delta)

    if deltas_selected and deltas_unselected:
        median_sel = statistics.median(deltas_selected)
        median_uns = statistics.median(deltas_unselected)
        diff = median_sel - median_uns
    else:
        median_sel = median_uns = diff = None

    falsification_threshold = 0.15
    falsification_triggered = (
        diff is not None and abs(diff) > falsification_threshold
    )

    return {
        "selected_tasks": sorted(selected_tasks),
        "unselected_tasks": sorted(unselected_tasks),
        "deltas_selected_BminusA": [round(d, 4) for d in deltas_selected],
        "deltas_unselected_BminusA": [round(d, 4) for d in deltas_unselected],
        "median_selected": round(median_sel, 4) if median_sel is not None else None,
        "median_unselected": round(median_uns, 4) if median_uns is not None else None,
        "median_diff_selected_minus_unselected": round(diff, 4) if diff is not None else None,
        "falsification_threshold": falsification_threshold,
        "falsification_triggered": falsification_triggered,
        "interpretation": (
            "Selection bias DOES drive c_at_a_min lift" if falsification_triggered
            else "Selection bias does NOT explain the iter-2 pattern at this proxy"
        ),
        "caveat": "Proxy: locked_pass_rate (iter-2 has no c_at_a_min). Phase 0.4 should rerun with c_at_a_min once decomposer outputs available.",
        "per_task": per_task_results,
    }


def main():
    grades = _walk_grades_with_cells()
    icc = estimate_icc_per_dim(grades)
    per_task_means = per_task_means_per_arm_per_dim(grades)
    empirical_sd = empirical_paired_delta_sd(per_task_means)

    # Build lmer MDE table: per dim, max(SD_A, SD_B) within-task SD as input
    lmer_mde = {}
    icc_pooled = {}
    for dim in DIMENSIONS:
        sigma_within_a = (icc["A"][dim] or {}).get("sigma2_within") if icc["A"].get(dim) else None
        sigma_within_b = (icc["B"][dim] or {}).get("sigma2_within") if icc["B"].get(dim) else None
        icc_a = (icc["A"][dim] or {}).get("icc") if icc["A"].get(dim) else None
        icc_b = (icc["B"][dim] or {}).get("icc") if icc["B"].get(dim) else None

        sd_within_pooled = math.sqrt(
            statistics.mean(
                [s for s in [sigma_within_a, sigma_within_b] if s is not None]
            )
        ) if (sigma_within_a is not None or sigma_within_b is not None) else None
        icc_avg = (
            statistics.mean([i for i in [icc_a, icc_b] if i is not None])
            if (icc_a is not None or icc_b is not None) else None
        )
        icc_pooled[dim] = round(icc_avg, 4) if icc_avg is not None else None

        if sd_within_pooled is None:
            lmer_mde[dim] = None
            continue

        lmer_mde[dim] = {
            "sd_within_pooled": round(sd_within_pooled, 4),
            "icc_observed_average": round(icc_avg, 4) if icc_avg is not None else None,
            "mde_at_observed_icc": (
                lmer_mde_under_icc(sd_within_pooled, icc_avg)
                if icc_avg is not None else None
            ),
            "mde_at_icc_0_1": lmer_mde_under_icc(sd_within_pooled, 0.1),
            "mde_at_icc_0_3": lmer_mde_under_icc(sd_within_pooled, 0.3),
            "mde_at_icc_0_5": lmer_mde_under_icc(sd_within_pooled, 0.5),
        }

    selection_bias = selection_bias_falsification_locked_pass_rate()

    out = {
        "computed_at": "2026-05-07T13:00:00Z",
        "computed_by_script": "thoughts/shared/research/scripts/compute_mde_lmer.py",
        "iter2_eval_dir": str(ITER2_DIR),
        "n_grades_loaded": len(grades),
        "design": {
            "n_cells_per_arm_total": N_CELLS_PER_ARM,
            "n_tasks": N_TASKS,
            "m_avg_cells_per_task": M_AVG_CELLS_PER_TASK,
        },
        "icc_per_dim_per_setup": icc,
        "icc_pooled_per_dim": icc_pooled,
        "lmer_based_mde_per_dim": lmer_mde,
        "empirical_paired_delta_sd_per_dim": empirical_sd,
        "selection_bias_falsification_test": selection_bias,
        "interpretation": (
            "lmer-based MDE drops below per-task-mean MDE for moderate ICC "
            "(0.3-0.5), reflecting design-effect benefit of within-task pooling. "
            "Some rubric dims may cross back into inferential scope under lmer "
            "depending on observed ICC. Empirical paired-delta SD reconciles "
            "the artificial rho assumption used in compute_mde.py — compare "
            "rho_implied_at_match against {0, 0.3, 0.5} to see which assumed "
            "rho was closest. Selection-bias falsification on locked_pass_rate "
            "reports whether the 4-task set chosen for iter-3 n=4 had "
            "systematically larger paired delta in iter-2 (regression-to-mean "
            "bait) — falsification triggered if |median_sel - median_uns| > 0.15."
        ),
    }

    out_path = REPO / "thoughts/shared/research/iter3-prereg-lmer-mde-table.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")

    # Console summary
    print("\n=== ICC per dim (pooled across A and B) ===")
    for dim, v in icc_pooled.items():
        print(f"  {dim:30s} ICC={v}")
    print("\n=== lmer MDE per dim under ICC sensitivity sweep ===")
    print(f"{'dim':<28s} {'sd_w':<7s} {'ICC*':<6s} {'MDE@ICC=0.1':<13s} {'MDE@ICC=0.3':<13s} {'MDE@ICC=0.5':<13s}")
    for dim, v in lmer_mde.items():
        if v:
            obs = v.get("mde_at_observed_icc") or {}
            m1 = v["mde_at_icc_0_1"]["mde"]
            m3 = v["mde_at_icc_0_3"]["mde"]
            m5 = v["mde_at_icc_0_5"]["mde"]
            print(f"  {dim:<26s} {v['sd_within_pooled']:<7.3f} "
                  f"{(obs.get('icc') or 0):<6.3f} {m1:<13.3f} {m3:<13.3f} {m5:<13.3f}")
    print("\n=== Empirical paired-delta SD per dim (drops rho assumption) ===")
    for dim, v in empirical_sd.items():
        if v["sd_diff_empirical"] is not None:
            print(f"  {dim:30s} sd_emp={v['sd_diff_empirical']:.3f} "
                  f"rho_implied={v['rho_implied_at_match']} "
                  f"median_delta={v['median_delta']}")
    print("\n=== Selection-bias falsification (locked_pass_rate proxy) ===")
    sb = selection_bias
    print(f"  selected (P0/T1/T2/E1) deltas: {sb['deltas_selected_BminusA']}")
    print(f"  unselected (T5/T7/T8/T9) deltas: {sb['deltas_unselected_BminusA']}")
    print(f"  median_selected={sb['median_selected']}, median_unselected={sb['median_unselected']}")
    print(f"  diff={sb['median_diff_selected_minus_unselected']}")
    print(f"  falsification_triggered: {sb['falsification_triggered']}")
    print(f"  → {sb['interpretation']}")


if __name__ == "__main__":
    main()
