#!/usr/bin/env python3
"""Aggregate per-task results from a run dir into report.{md,json}.

Usage:
    python3 eval/aggregate.py <run_dir> [--audit-dir /tmp/rc-events]

Inputs (read from <run_dir>):
    *.json                  -- per-task metrics written by run_task.sh,
                               named "<task_id>.<arm>.json".
    results.jsonl           -- per-(task,arm) wrapper records (optional).

Audit JSONL (optional):
    /tmp/rc-events/<date>/<session>.jsonl  -- one record per PreToolUse
    fire (treatment arm only). Joined by session_id when available.

Outputs:
    <run_dir>/report.json   -- machine-readable summary.
    <run_dir>/report.md     -- human-readable summary.

Metrics:
    Resolve rate (primary), regression rate (primary), 8 secondary metrics
    listed in EVAL_DESIGN.md section 3. Each metric reports paired
    difference mean, BCa 95% CI (10000 resamples), Wilcoxon p,
    Holm-Bonferroni adjusted p.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# Local imports (eval package).
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.stats import bca_bootstrap, holm_bonferroni, paired_wilcoxon  # noqa: E402


METRIC_KEYS: tuple[str, ...] = (
    "resolved_rate",
    "regression_rate",
    "ast_edit_distance",
    "cyclomatic_delta",
    "fan_in_delta",
    "fan_out_delta",
    "wall_clock_s",
    "tokens_in",
    "tokens_out",
    "novelty_drift",
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_per_task(run_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Return {task_id: {arm: per_task_dict}}."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for p in sorted(run_dir.glob("*.json")):
        if p.name in {"report.json", "run_summary.json"}:
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
        except Exception:
            continue
        tid = obj.get("task_id")
        arm = obj.get("arm")
        if not tid or not arm:
            continue
        out.setdefault(tid, {})[arm] = obj
    return out


def _load_audit(audit_dir: Path) -> list[dict[str, Any]]:
    if not audit_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for p in audit_dir.rglob("*.jsonl"):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
    return events


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def _per_task_metric(rec: dict[str, Any], key: str) -> float:
    if key == "resolved_rate":
        return 1.0 if rec.get("resolved") else 0.0
    if key == "regression_rate":
        return 1.0 if rec.get("regression_introduced") else 0.0
    if key == "wall_clock_s":
        return float(rec.get("wall_clock_s", 0) or 0)
    if key == "tokens_in":
        return float(rec.get("tokens_in", 0) or 0)
    if key == "tokens_out":
        return float(rec.get("tokens_out", 0) or 0)
    # Optional secondary metrics; default to 0.0 when absent.
    return float(rec.get(key, 0.0) or 0.0)


def _build_paired(records: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[tuple[float, float, float]]]:
    """Return {metric: [(treatment, vanilla, treatment-vanilla), ...]}."""
    paired: dict[str, list[tuple[float, float, float]]] = {k: [] for k in METRIC_KEYS}
    for tid, by_arm in sorted(records.items()):
        if "treatment" not in by_arm or "vanilla" not in by_arm:
            continue
        for k in METRIC_KEYS:
            t = _per_task_metric(by_arm["treatment"], k)
            v = _per_task_metric(by_arm["vanilla"], k)
            paired[k].append((t, v, t - v))
    return paired


# ---------------------------------------------------------------------------
# Decision criteria
# ---------------------------------------------------------------------------

def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _decision(per_metric: dict[str, dict[str, Any]], n_pairs: int) -> dict[str, Any]:
    rr = per_metric.get("regression_rate", {})
    resr = per_metric.get("resolved_rate", {})
    wc = per_metric.get("wall_clock_s", {})

    rr_delta = -float(rr.get("mean_delta", 0.0))  # vanilla - treatment
    rr_p = float(rr.get("p_holm", float("nan")))
    resr_delta = float(resr.get("mean_delta", 0.0))  # treatment - vanilla
    latency_ratio = _safe_div(float(wc.get("mean_treatment", 0.0)), float(wc.get("mean_vanilla", 1.0)))

    rr_pass = (rr_delta >= 0.15) and (not math.isnan(rr_p)) and (rr_p < 0.05)
    resr_pass = resr_delta >= -0.05
    latency_pass = latency_ratio <= 1.5

    criteria = {
        "regression_rate_drop_geq_0.15_holm_p_lt_0.05": {
            "rr_delta": rr_delta,
            "p_holm": rr_p,
            "pass": rr_pass,
        },
        "resolved_rate_no_worse_than_-5pp": {
            "resr_delta": resr_delta,
            "pass": resr_pass,
        },
        "latency_ratio_leq_1.5": {
            "latency_ratio": latency_ratio,
            "pass": latency_pass,
        },
    }
    overall_ship = rr_pass and resr_pass and latency_pass
    overall_kill = (rr_delta < -0.05) or (resr_delta < -0.10)
    if overall_ship:
        verdict = "ship"
    elif overall_kill:
        verdict = "kill"
    else:
        verdict = "inconclusive"
    return {
        "n_pairs": n_pairs,
        "criteria": criteria,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Report renderers
# ---------------------------------------------------------------------------

def _summarize_metric(deltas: list[float], treatments: list[float], vanillas: list[float]) -> dict[str, Any]:
    if not deltas:
        return {
            "n": 0,
            "mean_delta": float("nan"),
            "mean_treatment": float("nan"),
            "mean_vanilla": float("nan"),
            "ci_lo": float("nan"),
            "ci_hi": float("nan"),
            "p_wilcoxon": float("nan"),
            "p_holm": float("nan"),
        }
    mean_delta = sum(deltas) / len(deltas)
    mean_t = sum(treatments) / len(treatments)
    mean_v = sum(vanillas) / len(vanillas)
    ci_lo, ci_hi = bca_bootstrap(deltas, n=10000)
    p = paired_wilcoxon(deltas)
    return {
        "n": len(deltas),
        "mean_delta": float(mean_delta),
        "mean_treatment": float(mean_t),
        "mean_vanilla": float(mean_v),
        "ci_lo": float(ci_lo),
        "ci_hi": float(ci_hi),
        "p_wilcoxon": float(p),
        "p_holm": float("nan"),  # filled in after Holm correction
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    header = report.get("header", {})
    lines.append(f"# Eval Report -- {header.get('run_id', '?')}")
    lines.append("")
    lines.append(f"- pairs: {header.get('n_pairs', 0)}")
    lines.append(f"- generated_at: {header.get('generated_at', '?')}")
    lines.append("")
    lines.append("## Metrics (treatment - vanilla)")
    lines.append("")
    lines.append("| metric | n | mean_delta | 95% CI | p (wilcoxon) | p (holm) |")
    lines.append("|---|---:|---:|---|---:|---:|")
    for k in METRIC_KEYS:
        m = report["metrics"][k]
        ci = f"[{m['ci_lo']:.3f}, {m['ci_hi']:.3f}]" if not math.isnan(m["ci_lo"]) else "n/a"
        lines.append(
            f"| {k} | {m['n']} | {m['mean_delta']:.4f} | {ci} | {m['p_wilcoxon']:.4f} | {m['p_holm']:.4f} |"
        )
    lines.append("")
    lines.append("## Decision criteria")
    lines.append("")
    dec = report["decision"]
    lines.append(f"- verdict: **{dec['verdict']}**")
    for name, body in dec["criteria"].items():
        lines.append(f"- {name}: {'PASS' if body['pass'] else 'FAIL'}")
    lines.append("")
    lines.append("Operational counts: " + json.dumps(report.get("ops", {})))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def aggregate(run_dir: Path, audit_dir: Path | None = None) -> dict[str, Any]:
    records = _load_per_task(run_dir)
    paired = _build_paired(records)

    metrics_out: dict[str, dict[str, Any]] = {}
    pvals_aligned: list[float] = []
    for k in METRIC_KEYS:
        triples = paired[k]
        treatments = [t for t, _v, _d in triples]
        vanillas = [v for _t, v, _d in triples]
        deltas = [d for _t, _v, d in triples]
        m = _summarize_metric(deltas, treatments, vanillas)
        metrics_out[k] = m
        pvals_aligned.append(m["p_wilcoxon"])

    adjusted = holm_bonferroni(pvals_aligned)
    for k, p_adj in zip(METRIC_KEYS, adjusted):
        metrics_out[k]["p_holm"] = float(p_adj)

    n_pairs = max((m["n"] for m in metrics_out.values()), default=0)
    decision = _decision(metrics_out, n_pairs)

    audit_events: list[dict[str, Any]] = []
    if audit_dir is not None:
        audit_events = _load_audit(audit_dir)
    ops = {
        "tasks_seen": len(records),
        "complete_pairs": n_pairs,
        "audit_events": len(audit_events),
        "audit_blocks": sum(1 for e in audit_events if e.get("exit_code") == 2),
    }

    from datetime import datetime, timezone
    report = {
        "header": {
            "run_id": run_dir.name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_pairs": n_pairs,
        },
        "metrics": metrics_out,
        "decision": decision,
        "ops": ops,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=str)
    p.add_argument("--audit-dir", type=str, default="/tmp/rc-events")
    p.add_argument("--no-md", action="store_true")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        print(f"ERROR: run_dir {run_dir} does not exist", file=sys.stderr)
        return 1
    audit_dir = Path(args.audit_dir).resolve() if args.audit_dir else None

    report = aggregate(run_dir, audit_dir)

    with open(run_dir / "report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    if not args.no_md:
        with open(run_dir / "report.md", "w", encoding="utf-8") as fh:
            fh.write(_render_markdown(report))

    # Print headline.
    rr = report["metrics"]["regression_rate"]
    resr = report["metrics"]["resolved_rate"]
    print(
        f"[aggregate] pairs={report['header']['n_pairs']} "
        f"resolved_delta={resr['mean_delta']:+.3f} "
        f"regression_delta={rr['mean_delta']:+.3f} "
        f"verdict={report['decision']['verdict']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
