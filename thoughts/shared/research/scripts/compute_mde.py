"""
Compute Minimum Detectable Effect (MDE) for iter-3 metrics.

Methodology:
  Paired comparison MDE under t-distribution (small-n appropriate at k=8 task pairs):
      MDE_dz_paired_t = (t_{1-alpha/2, df} + t_{1-beta, df}) / sqrt(n_pairs)
  At alpha=0.05 two-sided, beta=0.20:
      n_pairs=8  (per-task means): t_{0.975,7} + t_{0.80,7} = 2.365 + 0.896 = 3.261
      n_pairs=28 (per-cell paired): t_{0.975,27} + t_{0.80,27} = 2.052 + 0.855 = 2.907
      n_pairs=56 (full bootstrap):  t_{0.975,55} + t_{0.80,55} ≈ 2.004 + 0.846 = 2.850

  Wilcoxon paired sign-rank ARE vs paired-t at small n is approximately:
      n=8  ARE ~= 0.93   -> inflate factor 1/sqrt(0.93) = 1.037
      n=28 ARE ~= 0.95   -> inflate factor 1/sqrt(0.95) = 1.026
      n=56 ARE ~= 0.955  -> inflate factor 1/sqrt(0.955) = 1.024

  SD of paired difference under correlation rho:
      SD_diff = sqrt(SD_A^2 + SD_B^2 - 2*rho*SD_A*SD_B)
  Reported under rho ∈ {0.0 (conservative), 0.3, 0.5} as sensitivity sweep.

Output:
  iter3-prereg-mde-table.json
"""
import json, math
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent.parent
ITER2_REPORT = Path("/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3/report.json")

# t-distribution upper-tail critical values (precomputed because scipy unavailable)
# z_{0.975}=1.960; z_{0.80}=0.842 (used for asymptotic n>>30)
T_CRIT = {
    7:   {"t_0975": 2.365, "t_0800": 0.896},   # df = n_pairs - 1 = 7 for n=8
    27:  {"t_0975": 2.052, "t_0800": 0.855},   # df = 27 for n=28
    55:  {"t_0975": 2.004, "t_0800": 0.846},   # df = 55 for n=56
}

WILCOXON_INFLATE_BY_N = {
    8:  1.037,
    28: 1.026,
    56: 1.024,
}

def mde_paired_t(sd_diff: float, n_pairs: int, wilcoxon: bool = False) -> float:
    df = n_pairs - 1
    crit = T_CRIT.get(df) or T_CRIT[55]   # default to large-n
    factor = crit["t_0975"] + crit["t_0800"]
    if wilcoxon:
        factor *= WILCOXON_INFLATE_BY_N.get(n_pairs, 1.024)
    return factor * sd_diff / math.sqrt(n_pairs)

def sd_diff(sd_a: float, sd_b: float, rho: float) -> float:
    return math.sqrt(sd_a**2 + sd_b**2 - 2*rho*sd_a*sd_b)

# Iter-2 SDs from REPORT.md "Paired deltas" table; iter-2 report.json arms
# wall_clock and tokens reported SD=0 in iter-2 (degenerate single-value medians);
# we use empirical per-task spread estimates as priors.
METRICS = {
    "flake_locked_pass_rate": {
        "sd_A": 0.059, "sd_B": 0.118, "median_delta": 0.0,
        "test": "Wilcoxon", "wilcoxon": True,
        "n_pairs_per_task": 8,
        "n_pairs_per_cell": 28,
        "operationally_meaningful": 0.10,
        "operationally_meaningful_source": "iter-1 paper §6.4: 0.10 absolute pass-rate fraction = 1 of 10 tests flipping; corresponds to crossing locked threshold floor by 0.10",
        "unit": "pass-rate fraction",
    },
    "flake_rotated_pass_rate": {
        "sd_A": 0.082, "sd_B": 0.127, "median_delta": -0.033,
        "test": "Wilcoxon", "wilcoxon": True,
        "n_pairs_per_task": 8, "n_pairs_per_cell": 28,
        "operationally_meaningful": 0.10,
        "operationally_meaningful_source": "iter-1 paper §6.4: same floor as locked",
        "unit": "pass-rate fraction",
    },
    "plan_impl_jaccard": {
        "sd_A": 0.099, "sd_B": 0.296, "median_delta": -0.178,
        "test": "Wilcoxon", "wilcoxon": True,
        "n_pairs_per_task": 8, "n_pairs_per_cell": None,
        "operationally_meaningful": 0.20,
        "operationally_meaningful_source": "iter-1 paper §6.6: jaccard delta of 0.20 corresponds to 1 in 5 plan claims diverging at impl",
        "unit": "jaccard ∈ [0,1]",
    },
    "wall_clock_s": {
        "sd_A": 150.0, "sd_B": 235.0, "median_delta": 134.0,
        "test": "Wilcoxon log", "wilcoxon": True,
        "n_pairs_per_task": 8, "n_pairs_per_cell": 28,
        "operationally_meaningful": 30.0,
        "operationally_meaningful_source": "developer-flow threshold: 30s ≈ 5% of iter-2 A median run wall-clock (493s); below this is dev-experience-equivalent",
        "unit": "seconds",
    },
    "impl_quality_mean": {
        "sd_A": 0.4, "sd_B": 0.4, "median_delta": 0.066,
        "test": "ordinal mixed-effects", "wilcoxon": False,
        "n_pairs_per_task": 8, "n_pairs_per_cell": None,
        "operationally_meaningful": 1.0,
        "operationally_meaningful_source": "BARS rubric anchor spacing: 1.0 = one anchor level (1→3 or 3→5 jump)",
        "unit": "anchor level (1/3/5)",
    },
    "plan_quality_mean": {
        "sd_A": 0.4, "sd_B": 0.4, "median_delta": 0.479,
        "test": "ordinal mixed-effects", "wilcoxon": False,
        "n_pairs_per_task": 8, "n_pairs_per_cell": None,
        "operationally_meaningful": 1.0,
        "operationally_meaningful_source": "BARS rubric anchor spacing",
        "unit": "anchor level (1/3/5)",
    },
    "honesty_signal_mean": {
        "sd_A": 0.4, "sd_B": 0.4, "median_delta": None,
        "test": "ordinal mixed-effects", "wilcoxon": False,
        "n_pairs_per_task": 8, "n_pairs_per_cell": None,
        "operationally_meaningful": 1.0,
        "operationally_meaningful_source": "BARS rubric anchor spacing (new dim, prior matches existing rubric dims)",
        "unit": "anchor level (1/3/5)",
    },
    "c_at_a_min": {
        "sd_A": 0.10, "sd_B": 0.15, "median_delta": None,
        "test": "Wilcoxon", "wilcoxon": True,
        "n_pairs_per_task": 8, "n_pairs_per_cell": 28,
        "operationally_meaningful": 0.10,
        "operationally_meaningful_source": "matches flake_pass_rate threshold: 0.10 fraction = crossing typical decision threshold",
        "unit": "fraction ∈ [0,1]",
    },
    "total_dollars_mean": {
        "sd_A": 0.5, "sd_B": 0.5, "median_delta": None,
        "test": "Wilcoxon log", "wilcoxon": True,
        "n_pairs_per_task": 8, "n_pairs_per_cell": 28,
        "operationally_meaningful": 0.10,
        "operationally_meaningful_source": "10% relative cost delta = ops-budget-meaningful threshold",
        "unit": "USD per cell (relative log scale)",
    },
}

RHO_VALUES = [0.0, 0.3, 0.5]

def build_table():
    rows = []
    for name, m in METRICS.items():
        per_rho = {}
        for rho in RHO_VALUES:
            sd_d = sd_diff(m["sd_A"], m["sd_B"], rho)
            per_task_mde = mde_paired_t(sd_d, m["n_pairs_per_task"], wilcoxon=m["wilcoxon"])
            per_cell_mde = (mde_paired_t(sd_d, m["n_pairs_per_cell"], wilcoxon=m["wilcoxon"])
                           if m["n_pairs_per_cell"] else None)
            per_rho[f"rho_{rho:.1f}"] = {
                "sd_diff": round(sd_d, 4),
                "mde_per_task_n8": round(per_task_mde, 4),
                "mde_per_cell_n28": round(per_cell_mde, 4) if per_cell_mde else None,
            }

        # Detectability under primary (rho=0.3 = realistic prior)
        primary = per_rho["rho_0.3"]
        op_thr = m["operationally_meaningful"]
        det_per_task = primary["mde_per_task_n8"] <= op_thr
        det_per_cell = (primary["mde_per_cell_n28"] is not None
                        and primary["mde_per_cell_n28"] <= op_thr)

        rows.append({
            "metric": name,
            "iter2_sd_A": m["sd_A"],
            "iter2_sd_B": m["sd_B"],
            "iter2_observed_delta": m["median_delta"],
            "test": m["test"],
            "operationally_meaningful_threshold": op_thr,
            "operationally_meaningful_threshold_source": m["operationally_meaningful_source"],
            "unit": m["unit"],
            "mde_under_rho_sensitivity": per_rho,
            "detectable_at_op_threshold_per_task_n8_rho_0_3": det_per_task,
            "detectable_at_op_threshold_per_cell_n28_rho_0_3": det_per_cell,
            "primary_estimator": "lmer mixed-effects" if not m["wilcoxon"] else "Wilcoxon paired sign-rank",
            "mde_caveat": "MDE computed under paired-difference normality (paired-t); Wilcoxon ARE inflation applied. lmer-based effective n typically larger than n_pairs_per_task=8 due to within-task pooling — these MDE values are the conservative per-task-mean baseline, not the lmer-adjusted floor.",
        })

    detectable_per_task = [r["metric"] for r in rows if r["detectable_at_op_threshold_per_task_n8_rho_0_3"]]
    detectable_per_cell = [r["metric"] for r in rows if r["detectable_at_op_threshold_per_cell_n28_rho_0_3"]]

    return {
        "computed_at": "2026-05-07T08:30:00Z",
        "computed_by_script": "thoughts/shared/research/scripts/compute_mde.py",
        "method": "paired_t_with_t_distribution_critical_values_inflated_for_wilcoxon",
        "alpha_two_sided": 0.05,
        "beta": 0.20,
        "rho_sensitivity_values": RHO_VALUES,
        "rho_primary_for_detectability_classification": 0.3,
        "n_pairs_per_task_used": 8,
        "n_pairs_per_cell_used": 28,
        "n_cells_per_arm_total": 28,
        "n_cells_total": 56,
        "n_runs_by_task": {"P0":4,"T1":4,"T2":4,"E1":4,"T5":3,"T7":3,"T8":3,"T9":3},
        "metrics": rows,
        "summary": {
            "detectable_at_op_threshold_per_task_n8": detectable_per_task,
            "detectable_at_op_threshold_per_cell_n28": detectable_per_cell,
            "iter_3_can_make_inferential_claims_about": detectable_per_task + [
                m for m in detectable_per_cell if m not in detectable_per_task
            ],
            "iter_3_must_report_descriptively_not_inferentially": [
                r["metric"] for r in rows
                if not r["detectable_at_op_threshold_per_task_n8_rho_0_3"]
                and not r["detectable_at_op_threshold_per_cell_n28_rho_0_3"]
            ],
        },
        "interpretation": (
            "MDE classifies which metrics iter-3 can make INFERENTIAL claims about (effect size > MDE => "
            "non-zero effect detectable at alpha=0.05/beta=0.20). All other metrics must be reported "
            "DESCRIPTIVELY in iter-3 paper §8.2: per-arm means + per-task tables, but NOT 'B is N% higher than A'-style "
            "inferential framing. Affirmative scope of iter-3 inferential claims = 'iter_3_can_make_inferential_claims_about' list."
        ),
    }

if __name__ == "__main__":
    out = build_table()
    out_path = Path(__file__).parent.parent / "iter3-prereg-mde-table.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")
    print(f"Detectable per-task n=8: {out['summary']['detectable_at_op_threshold_per_task_n8']}")
    print(f"Detectable per-cell n=28: {out['summary']['detectable_at_op_threshold_per_cell_n28']}")
    print(f"Inferential claims allowed for: {out['summary']['iter_3_can_make_inferential_claims_about']}")
    print(f"Descriptive only: {out['summary']['iter_3_must_report_descriptively_not_inferentially']}")
