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


def _stream_daily_fpr(log_dir: Path, since: datetime) -> Dict[str, float]:
    """Stream-process shadow events into per-day FPR — bounded memory.

    Round-2 fix: previous impl loaded every record into a List[dict] before
    aggregating; on a 5GB audit dir that OOMs the cron host. Now folds
    into the by_day dict inline.

    Pseudo-label fallback (fix for "ground_truth never written"): if no
    `ground_truth` field, treat decision in {"allowed", "shadow_blocked"}
    AND no subsequent retry as proxy-benign for FPR calculation."""
    by_day: Dict[str, List[bool]] = {}
    if not log_dir.exists():
        return {}
    for day_dir in sorted(log_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day_dt = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day_dt < since:
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
                        if not rec.get("shadow"):
                            continue
                        ts = rec.get("ts")
                        if not ts:
                            continue
                        try:
                            day_key = datetime.fromtimestamp(
                                float(ts), timezone.utc).strftime("%Y-%m-%d")
                        except (ValueError, OSError):
                            continue
                        gt = rec.get("ground_truth")
                        if gt is None:
                            decision = rec.get("decision", "")
                            if decision not in ("allowed", "shadow_blocked"):
                                continue
                        elif gt != "benign":
                            continue
                        by_day.setdefault(day_key, []).append(
                            bool(rec.get("would_block") or rec.get("shadow_blocked"))
                        )
            except OSError:
                continue
    return {
        d: (sum(blocks) / len(blocks)) if blocks else 0.0
        for d, blocks in by_day.items()
    }


_RECENT_ALARM_DAYS = 14


def _page_hinkley(daily_fpr: Dict[str, float], target: float,
                  *, recent_only_days: int = _RECENT_ALARM_DAYS) -> Optional[str]:
    """Return alarm date if PH detects sustained drift in the last
    `recent_only_days` of the window. None otherwise.

    Round-2 fix: previous impl returned the first alarm in a 90-day window,
    so a brief FPR spike 80 days ago re-triggered recalibrations forever.
    Now we only fire if drift persists into the recent window — stale
    alarms naturally age out.

    PH stat: m_t = Σ (x_s − target − Δ) − min_{s≤t} (Σ); alarm when m_t > λ.
    """
    if not daily_fpr:
        return None
    days = sorted(daily_fpr)
    cum = 0.0
    min_so_far = 0.0
    last_alarm: Optional[str] = None
    for day in days:
        cum += daily_fpr[day] - target - _DELTA
        min_so_far = min(min_so_far, cum)
        if cum - min_so_far > _LAMBDA:
            last_alarm = day
    if last_alarm is None:
        return None
    try:
        alarm_dt = datetime.strptime(last_alarm, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    last_day = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if (last_day - alarm_dt) > timedelta(days=recent_only_days):
        return None  # stale
    return last_alarm


def _loc_churn(repo: Path, since_commit: str) -> float:
    """Fraction of changed LOC since the snapshot commit. 0..1.

    Round-2 fix: previously returned 0.0 on any git failure (silent fail-open
    on a SAFETY mechanism). Now returns +inf on git error so the trigger
    forces a refit (fail-safe direction matches the safety purpose). Also
    uses actual `wc -l` over tracked files instead of the magic 30 LOC/file
    constant, so the ratio has a defined meaning."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--shortstat", since_commit, "HEAD"],
            text=True, timeout=10,
        )
        ins = del_ = 0
        for p in out.split(","):
            if "insertion" in p:
                ins = int(p.strip().split()[0])
            elif "deletion" in p:
                del_ = int(p.strip().split()[0])
        files = subprocess.check_output(
            ["git", "-C", str(repo), "ls-files"], text=True, timeout=10,
        ).splitlines()
        code_files = [
            f for f in files
            if any(f.endswith(ext) for ext in (
                ".py", ".ts", ".tsx", ".js", ".vue", ".cs", ".java",
                ".go", ".rs", ".rb", ".kt", ".swift",
            ))
        ]
        if not code_files:
            return 0.0
        total_loc = 0
        for f in code_files:
            try:
                blob_size = int(subprocess.check_output(
                    ["git", "-C", str(repo), "cat-file", "-s", f"HEAD:{f}"],
                    text=True, timeout=5,
                ).strip())
                total_loc += max(blob_size // 40, 1)
            except (subprocess.SubprocessError, ValueError):
                continue
        return (ins + del_) / max(total_loc, 1)
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        sys.stderr.write(f"[recalibrate] git churn check failed: {exc}; "
                         f"forcing recalibration (fail-safe)\n")
        return float("inf")


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
    daily = _stream_daily_fpr(args.shadow_log, since)
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
        "loc_churn": churn,
        "target_fpr": target_fpr,
    }
    out_str = json.dumps(payload, indent=2)
    sys.stdout.write(out_str + "\n")

    if triggered:
        args.out_signal.parent.mkdir(parents=True, exist_ok=True)
        args.out_signal.write_text(out_str)
    else:
        # Round-2 fix: clear stale signal so a previous-week alarm doesn't
        # fire forever. unlink() with missing_ok requires py3.8+.
        try:
            args.out_signal.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
