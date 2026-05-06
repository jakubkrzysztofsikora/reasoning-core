"""Monthly Page-Hinkley / CUSUM recalibration trigger (P7).

Reads recent shadow-mode telemetry (rolling 90-day window) and decides
whether the calibrated Mahalanobis model needs refitting. Two triggers:

1. **Page-Hinkley CUSUM** on per-day FPR — if the current 7-day mean FPR
   drifts above the calibrated FPR target by ≥ Δ=0.02 sustained for ≥ 3
   days, recalibrate.
2. **LOC churn** — recalibrate after >20% LOC churn vs the snapshot used
   at last fit (per plan §P7 reviewer correction).

Designed to run from cron / launchd weekly; emits a sentinel file
`eval/runs/recalibrate.signal` consumed by the supervisor on next boot.

Usage
-----
    python -m eval.recalibrate \
        --shadow-log ~/.local/share/reasoning-core/events/ \
        --calib-snapshot eval/runs/calibration.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

_DELTA = 0.02     # FPR-drift sensitivity
_LAMBDA = 0.06    # PH alarm threshold
_CHURN_PCT = 0.20  # >20% churn forces recalibration


def _read_shadow_events(log_dir: Path, since: datetime) -> List[dict]:
    out: List[dict] = []
    if not log_dir.exists():
        return out
    for day_dir in sorted(log_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day < since:
            continue
        for path in sorted(day_dir.iterdir()):
            opener = gzip.open if path.suffix == ".gz" else open
            try:
                with opener(path, "rt") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("shadow"):
                            out.append(rec)
            except OSError:
                continue
    return out


def _daily_fpr(events: List[dict]) -> Dict[str, float]:
    """{YYYY-MM-DD: fpr} where fpr = shadow_blocks / total on benign-labeled."""
    by_day: Dict[str, List[bool]] = {}
    for r in events:
        ts = r.get("ts")
        if not ts:
            continue
        try:
            day = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            continue
        if r.get("ground_truth") == "benign":
            by_day.setdefault(day, []).append(bool(r.get("would_block")))
    return {
        d: (sum(blocks) / len(blocks)) if blocks else 0.0
        for d, blocks in by_day.items()
    }


def _page_hinkley(daily_fpr: Dict[str, float], target: float) -> Optional[str]:
    """Return the date (YYYY-MM-DD) at which the PH stat first exceeds λ,
    indicating sustained drift above target+Δ. None if no alarm.

    PH = Σ_t (x_t − target − Δ); reset to 0 at min, alarm when current −
    running_min > λ.
    """
    if not daily_fpr:
        return None
    days = sorted(daily_fpr)
    cum = 0.0
    min_so_far = 0.0
    for day in days:
        cum += daily_fpr[day] - target - _DELTA
        min_so_far = min(min_so_far, cum)
        if cum - min_so_far > _LAMBDA:
            return day
    return None


def _loc_churn(repo: Path, since_commit: str) -> float:
    """Fraction of changed LOC since the snapshot commit. 0..1."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--shortstat", since_commit, "HEAD"],
            text=True, timeout=10,
        )
        # `123 files changed, 4567 insertions(+), 234 deletions(-)`
        parts = out.split(",")
        ins = del_ = 0
        for p in parts:
            if "insertion" in p:
                ins = int(p.strip().split()[0])
            elif "deletion" in p:
                del_ = int(p.strip().split()[0])
        total = subprocess.check_output(
            ["git", "-C", str(repo), "ls-files"], text=True, timeout=10,
        )
        loc = sum(
            1 for f in total.splitlines()
            if any(f.endswith(ext) for ext in (".py", ".ts", ".tsx", ".js", ".cs", ".java", ".go", ".rs"))
        )
        return (ins + del_) / max(loc * 30, 1)  # ~30 LOC/file rough estimate
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-log", type=Path,
                    default=Path(os.path.expanduser(
                        "~/.local/share/reasoning-core/events/")))
    ap.add_argument("--calib-snapshot", type=Path,
                    default=REPO_ROOT / "eval" / "runs" / "calibration.json")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out-signal", type=Path,
                    default=REPO_ROOT / "eval" / "runs" / "recalibrate.signal")
    args = ap.parse_args()

    snapshot: Dict = {}
    if args.calib_snapshot.exists():
        try:
            snapshot = json.loads(args.calib_snapshot.read_text())
        except (OSError, json.JSONDecodeError):
            snapshot = {}

    target_fpr = float(snapshot.get("fpr_target", 0.02))
    since_commit = snapshot.get("git_commit", "HEAD~100")

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    events = _read_shadow_events(args.shadow_log, since)
    daily = _daily_fpr(events)
    ph_alarm_day = _page_hinkley(daily, target_fpr)
    churn = _loc_churn(REPO_ROOT, since_commit)

    triggered = ph_alarm_day is not None or churn > _CHURN_PCT
    reasons = []
    if ph_alarm_day:
        reasons.append(f"page_hinkley_alarm:{ph_alarm_day}")
    if churn > _CHURN_PCT:
        reasons.append(f"loc_churn:{churn:.2%}")

    payload = {
        "triggered": triggered,
        "reasons": reasons,
        "n_days_observed": len(daily),
        "n_shadow_events": len(events),
        "loc_churn": churn,
        "target_fpr": target_fpr,
    }
    out_str = json.dumps(payload, indent=2)
    sys.stdout.write(out_str + "\n")

    if triggered:
        args.out_signal.parent.mkdir(parents=True, exist_ok=True)
        args.out_signal.write_text(out_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
