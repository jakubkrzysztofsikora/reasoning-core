"""Synthetic drift trajectory generator (P7).

Produces controlled drift sequences with known pivot points so the CUSUM
detector can be calibrated to a 5% type-I error rate. Outputs the
`cumulative_drift` warn/deny thresholds (replacing v1's placeholder
4.0 / 6.0 — reviewer correction #19).

Mechanism
---------
1. Generate N synthetic 9-d "session" sequences. Each sequence is a
   benign drift random walk (sigma=0.3 per step) of length T.
2. In half the sequences, inject a pivot at a uniformly random t* in
   [T/4, 3T/4]: post-pivot draws shift mean by Δ=2.0 in a random
   subspace.
3. CUSUM over Mahalanobis distance against a calibrated benign null
   detects pivots. Tune the cumulative_drift threshold so that on the
   no-pivot half the false-positive rate ≤ 5% (type-I).

Outputs
-------
JSON to stdout (or --out) with: warn_thr, deny_thr, type_I_at_warn,
power_at_deny, n_sequences. Plug warn/deny into env knobs RC_DRIFT_WARN /
RC_DRIFT_DENY.

Usage
-----
    python -m eval.synthetic_drift \
        --n 1000 --T 30 --out eval/runs/drift_thresholds.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError("synthetic_drift requires numpy") from exc

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import calibration  # type: ignore  # noqa: E402


def _benign_walk(rng: np.random.Generator, T: int, dim: int,
                 sigma: float = 0.3) -> np.ndarray:
    """Drift random walk in R^dim, starting at 0."""
    steps = rng.normal(0.0, sigma, size=(T, dim))
    return np.cumsum(steps, axis=0)


def _pivot_walk(rng: np.random.Generator, T: int, dim: int, *,
                sigma: float = 0.3, delta: float = 2.0,
                mode: str = "step") -> Tuple[np.ndarray, int]:
    """Walk with mean shift at a random pivot t*.

    mode='step' (default): post-pivot draws have constant offset δ — the
    realistic single-commit pivot. mode='ramp': linear drift post-pivot
    (legacy; kept for backwards-compat tests)."""
    t_star = rng.integers(T // 4, max(T // 4 + 1, 3 * T // 4))
    seq = _benign_walk(rng, T, dim, sigma)
    direction = rng.normal(size=dim)
    direction /= np.linalg.norm(direction) + 1e-9
    shift = delta * direction
    if mode == "step":
        seq[t_star:] += shift
    else:  # ramp
        seq[t_star:] += np.cumsum(
            np.tile(shift / max(T - t_star, 1), (T - t_star, 1)), axis=0
        )
    return seq, int(t_star)


def _cusum(scores: np.ndarray, *, ref: float = 0.0,
           drift: float = 0.5) -> np.ndarray:
    """Standard Page-Hinkley CUSUM statistic for upward shifts.

    M_t = S_t - min_{s<=t} S_s where S = cumsum(scores - ref - drift).
    Round-2 fix: previous impl used `S.min(initial=0)` (global min) which
    leaked future information into past timesteps.
    """
    S = np.cumsum(scores - ref - drift)
    return S - np.minimum.accumulate(S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000, help="sequences (half pivot)")
    ap.add_argument("--T", type=int, default=30, help="steps per sequence")
    ap.add_argument("--dim", type=int, default=9)
    ap.add_argument("--type-I", type=float, default=0.05)
    ap.add_argument("--power-target", type=float, default=0.9,
                    help="recall target for deny threshold")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    # 1. Calibrate Mahalanobis on a held-out benign sample.
    benign_calib = np.vstack([
        _benign_walk(rng, args.T, args.dim) for _ in range(200)
    ])
    model = calibration.fit(benign_calib, fpr_target=args.type_I)

    # 2. Generate test sequences: half benign, half pivoted.
    half = args.n // 2
    benign_seqs = [_benign_walk(rng, args.T, args.dim) for _ in range(half)]
    pivot_seqs = [_pivot_walk(rng, args.T, args.dim) for _ in range(half)]

    # 3. Per-sequence: compute per-step Mahalanobis distance, CUSUM, and
    #    take max CUSUM as the "cumulative drift" statistic for that session.
    def _cum_drift(seq: np.ndarray) -> float:
        per_step = np.array([
            calibration.score(model, seq[t]) for t in range(seq.shape[0])
        ])
        return float(_cusum(per_step).max())

    benign_stats = np.array([_cum_drift(s) for s in benign_seqs])
    pivot_stats = np.array([_cum_drift(s) for s, _ in pivot_seqs])

    # 4. Warn threshold = (1-type_I) quantile of benign CUSUM.
    warn_thr = float(np.quantile(benign_stats, 1.0 - args.type_I))
    # Deny threshold = quantile that achieves power_target on pivot stats.
    deny_thr = float(np.quantile(pivot_stats, 1.0 - args.power_target))
    deny_thr_fallback = False
    if deny_thr < warn_thr:
        # Power target unreachable on synthetic distribution — surface as
        # explicit flag so callers can refuse to promote thresholds.
        deny_thr = warn_thr * 1.5
        deny_thr_fallback = True

    type_I_at_warn = float((benign_stats > warn_thr).mean())
    power_at_deny = float((pivot_stats > deny_thr).mean())

    out = {
        "warn_thr": warn_thr,
        "deny_thr": deny_thr,
        "deny_thr_fallback": deny_thr_fallback,
        "type_I_at_warn": type_I_at_warn,
        "power_at_deny": power_at_deny,
        "n_sequences": args.n,
        "T": args.T,
        "dim": args.dim,
        "seed": args.seed,
        "calibration_threshold": model.threshold,
    }
    payload = json.dumps(out, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
        # Round-2 fix: when --out set, send human summary to stderr so CI can
        # capture clean JSON via `python -m eval.synthetic_drift > thr.json`.
        sys.stderr.write(f"[synthetic-drift] wrote {args.out}; "
                         f"warn={warn_thr:.3f} deny={deny_thr:.3f} "
                         f"fallback={deny_thr_fallback}\n")
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
