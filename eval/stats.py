"""Pure statistical helpers for the eval aggregator.

All functions accept lists/tuples of floats and return native floats. They
prefer scipy / statsmodels when available but ship stdlib fallbacks so the
test suite runs in a minimal environment.

Public surface (per RC-208 spec):

    paired_wilcoxon(deltas)             -> p (two-sided)
    bca_bootstrap(deltas, n=10000)      -> (lo, hi) 95% CI on mean
    holm_bonferroni(pvals)              -> [adjusted_pvals]

Each function tolerates short / degenerate inputs (returns NaN for p, the
mean as both bounds for a 1-element bootstrap, etc.) so the aggregator can
emit a report on n=1 smoke runs without exploding.
"""

from __future__ import annotations

import math
import random
from typing import Sequence


def _try_scipy_wilcoxon(deltas: Sequence[float]) -> float | None:
    try:
        from scipy.stats import wilcoxon  # type: ignore
    except Exception:
        return None
    try:
        # zero_method='wilcox' (the default) drops zero-differences -- matches
        # EVAL_DESIGN section 5.5.
        result = wilcoxon(list(deltas), zero_method="wilcox", alternative="two-sided")
        return float(result.pvalue)
    except Exception:
        return None


def _normal_sf(z: float) -> float:
    """Survival function of standard normal: P(Z > z). stdlib only."""
    # 0.5 * erfc(z / sqrt(2))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _wilcoxon_normal_approx(deltas: Sequence[float]) -> float:
    """Two-sided Wilcoxon signed-rank p-value via the normal approximation.

    Drops zero deltas (wilcox zero-method). Handles ties via average ranks
    and applies the standard tie correction to the variance.
    """
    nonzero = [d for d in deltas if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return float("nan")
    if n == 1:
        # Sign test on a single observation has p = 1.0 two-sided.
        return 1.0

    abs_vals = [abs(d) for d in nonzero]
    # Rank with average for ties.
    paired = sorted(range(n), key=lambda i: abs_vals[i])
    ranks = [0.0] * n
    i = 0
    tie_correction = 0.0
    while i < n:
        j = i
        while j + 1 < n and abs_vals[paired[j + 1]] == abs_vals[paired[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # ranks 1-indexed
        for k in range(i, j + 1):
            ranks[paired[k]] = avg_rank
        t = j - i + 1
        if t > 1:
            tie_correction += (t * t * t - t)
        i = j + 1

    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, nonzero) if d < 0)
    w = min(w_plus, w_minus)

    mean = n * (n + 1) / 4.0
    var = n * (n + 1) * (2 * n + 1) / 24.0 - tie_correction / 48.0
    if var <= 0:
        return float("nan")
    # Continuity correction.
    z = (w - mean + 0.5) / math.sqrt(var)
    if z > 0:
        z = 0.0  # w <= mean by construction; guard FP drift
    p = 2.0 * _normal_sf(-z)
    if p > 1.0:
        p = 1.0
    if p < 0.0:
        p = 0.0
    return float(p)


def paired_wilcoxon(deltas: Sequence[float]) -> float:
    """Two-sided Wilcoxon signed-rank p-value on paired differences.

    Uses scipy when present; otherwise a stdlib normal approximation. NaN if
    all deltas are zero (no signed information).
    """
    p = _try_scipy_wilcoxon(deltas)
    if p is not None and not math.isnan(p):
        return float(p)
    return _wilcoxon_normal_approx(deltas)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0,1])."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def _normal_inv_cdf(p: float) -> float:
    """Beasley-Springer-Moro inverse CDF approximation. Acceptable for BCa."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    # Beasley-Springer-Moro
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
           ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def bca_bootstrap(
    deltas: Sequence[float],
    n: int = 10000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float]:
    """BCa 95% confidence interval on the mean of ``deltas``.

    Falls back gracefully on degenerate inputs:
      - empty: (nan, nan)
      - single element x: (x, x)
      - all-equal: (mean, mean)

    Uses scipy.stats.bootstrap when available for fidelity, else a stdlib
    BCa implementation. The stdlib path is slower but still tractable at
    n=10000 on realistic eval sizes.
    """
    arr = list(deltas)
    if not arr:
        return (float("nan"), float("nan"))
    if len(arr) == 1:
        return (float(arr[0]), float(arr[0]))
    if all(x == arr[0] for x in arr):
        return (float(arr[0]), float(arr[0]))

    # Try scipy.
    try:
        import numpy as _np  # type: ignore
        from scipy.stats import bootstrap  # type: ignore

        rng = _np.random.default_rng(seed)
        result = bootstrap(
            (arr,),
            statistic=_np.mean,
            n_resamples=n,
            confidence_level=1 - alpha,
            method="BCa",
            random_state=rng,
        )
        ci = result.confidence_interval
        return (float(ci.low), float(ci.high))
    except Exception:
        pass

    # Stdlib fallback.
    rnd = random.Random(seed)
    m = len(arr)
    mean_obs = sum(arr) / m
    boot_means: list[float] = []
    for _ in range(n):
        # Sample m with replacement.
        s = 0.0
        for _ in range(m):
            s += arr[rnd.randrange(m)]
        boot_means.append(s / m)
    boot_means.sort()

    # Bias-correction z0.
    p0 = sum(1 for v in boot_means if v < mean_obs) / float(n)
    if p0 <= 0.0:
        p0 = 0.5 / n
    if p0 >= 1.0:
        p0 = 1.0 - 0.5 / n
    z0 = _normal_inv_cdf(p0)

    # Acceleration via jackknife.
    jk_means: list[float] = []
    total = sum(arr)
    for i in range(m):
        jk_means.append((total - arr[i]) / (m - 1))
    jk_mean = sum(jk_means) / m
    num = sum((jk_mean - v) ** 3 for v in jk_means)
    den = 6.0 * (sum((jk_mean - v) ** 2 for v in jk_means)) ** 1.5
    a = num / den if den != 0 else 0.0

    z_lo = _normal_inv_cdf(alpha / 2.0)
    z_hi = _normal_inv_cdf(1.0 - alpha / 2.0)
    alpha_lo = _normal_cdf(z0 + (z0 + z_lo) / max(1e-12, 1.0 - a * (z0 + z_lo)))
    alpha_hi = _normal_cdf(z0 + (z0 + z_hi) / max(1e-12, 1.0 - a * (z0 + z_hi)))
    alpha_lo = min(max(alpha_lo, 0.0), 1.0)
    alpha_hi = min(max(alpha_hi, 0.0), 1.0)

    lo = _percentile(boot_means, alpha_lo)
    hi = _percentile(boot_means, alpha_hi)
    return (float(lo), float(hi))


def holm_bonferroni(pvals: Sequence[float], alpha: float = 0.05) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values.

    Returns a list aligned with the input order. NaN p-values are left as
    NaN and excluded from rank counting (treated as missing). Uses
    statsmodels when available for parity with the reference impl.
    """
    try:
        from statsmodels.stats.multitest import multipletests  # type: ignore

        valid_idx = [i for i, p in enumerate(pvals) if not (p is None or (isinstance(p, float) and math.isnan(p)))]
        if not valid_idx:
            return [float("nan")] * len(pvals)
        valid_p = [pvals[i] for i in valid_idx]
        _, adj, _, _ = multipletests(valid_p, alpha=alpha, method="holm")
        out = [float("nan")] * len(pvals)
        for j, i in enumerate(valid_idx):
            out[i] = float(adj[j])
        return out
    except Exception:
        pass

    # Stdlib fallback: Holm step-down.
    pairs = [(i, p) for i, p in enumerate(pvals) if not (p is None or (isinstance(p, float) and math.isnan(p)))]
    pairs.sort(key=lambda x: x[1])
    m = len(pairs)
    adj_sorted: list[float] = []
    running_max = 0.0
    for k, (_, p) in enumerate(pairs):
        a = (m - k) * p
        if a > 1.0:
            a = 1.0
        if a < running_max:
            a = running_max
        running_max = a
        adj_sorted.append(a)

    out = [float("nan")] * len(pvals)
    for (orig_i, _), a in zip(pairs, adj_sorted):
        out[orig_i] = float(a)
    return out


def sign_test(wins: int, total: int, *, alternative: str = "greater") -> float:
    """Binomial sign-test p-value for the iter-2 falsifiable goal.

    Plan §"Falsifiable goal": ≥7/8 task-mean wins; sign-test 8/8 → p=0.0039,
    7/8 → p=0.035 (binomial under H0: P(win)=0.5, one-sided).

    Args:
        wins: number of tasks Setup B beat Setup A (≥1.0 BARS gap).
        total: total comparable tasks.
        alternative: "greater" (B>A; default — matches the plan's claim),
                     "less" (B<A), or "two-sided".

    Returns:
        One-sided (or two-sided) p-value under the binomial null P=0.5.
        Uses scipy.stats.binomtest when available, stdlib fallback otherwise.
    """
    if total < 1 or wins < 0 or wins > total:
        raise ValueError(f"sign_test: invalid wins={wins}/total={total}")
    if alternative not in ("greater", "less", "two-sided"):
        raise ValueError(f"unknown alternative: {alternative}")

    try:
        from scipy.stats import binomtest  # type: ignore
        return float(binomtest(wins, total, p=0.5, alternative=alternative).pvalue)
    except ImportError:
        pass

    # Stdlib fallback — exact binomial via cumulative sum.
    from math import comb

    def _pmf(k: int, n: int) -> float:
        return comb(n, k) / (2.0 ** n)

    if alternative == "greater":
        return sum(_pmf(k, total) for k in range(wins, total + 1))
    if alternative == "less":
        return sum(_pmf(k, total) for k in range(0, wins + 1))
    # two-sided: 2 * min(P(X>=wins), P(X<=wins)), clamped to 1.
    p_hi = sum(_pmf(k, total) for k in range(wins, total + 1))
    p_lo = sum(_pmf(k, total) for k in range(0, wins + 1))
    return min(1.0, 2.0 * min(p_hi, p_lo))


__all__ = [
    "paired_wilcoxon",
    "bca_bootstrap",
    "holm_bonferroni",
    "sign_test",
]
