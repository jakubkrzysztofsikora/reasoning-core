"""Qwen grounding eval (P5 gate).

Validates the local Qwen2.5-Coder-1.5B critic's plan→diff grounding
judgments against a stronger teacher (default: GPT-4-class or 70B local
on `RC_TEACHER_URL`). Computes Cohen's kappa over a labeled set of
(plan_claim, diff_hunk, teacher_label) triples.

Gate: Cohen's kappa >= 0.7 before CDGS (P2 plan grounding) is allowed
to consume Qwen judgments. Below 0.7 → keep BM25 fallback path active.

Usage:
    python -m eval.qwen_grounding_eval \
        --pairs eval/datasets/grounding_pairs.jsonl \
        --out eval/runs/qwen_grounding_<date>.json

Pairs file format (jsonl):
    {"id": "...", "claim": "...", "hunk": "...", "label": 0|1}

Label semantics: 1 = claim supported by hunk, 0 = not supported.

Reviewer corrections folded in:
- Per-call hard budget via RC_GEN_BUDGET_MS (no agent-visible timeouts).
- Failures (None) count as 0 (treats unreachable critic as "unsupported"
  — pessimistic, prevents critic-down from inflating kappa).
- Bootstrapped 95% CI on kappa (n=1000 resamples) — point estimate alone
  isn't a gate criterion.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import gen_client  # type: ignore  # noqa: E402


def _cohens_kappa(a: List[int], b: List[int]) -> float:
    assert len(a) == len(b) and a, "non-empty equal-length sequences required"
    n = len(a)
    agree = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    chance = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if chance >= 1.0:
        return 1.0 if agree >= 1.0 else 0.0
    return (agree - chance) / (1.0 - chance)


def _bootstrap_ci(a: List[int], b: List[int], *, n_boot: int = 1000,
                  seed: int = 7) -> Tuple[float, float]:
    rng = random.Random(seed)
    n = len(a)
    samples = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        sa = [a[i] for i in idx]
        sb = [b[i] for i in idx]
        try:
            samples.append(_cohens_kappa(sa, sb))
        except AssertionError:
            continue
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[int(0.975 * len(samples))]
    return lo, hi


def _load_pairs(path: Path) -> List[dict]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _qwen_label(claim: str, hunk: str) -> int:
    res = gen_client.score_plan_grounding(claim, hunk)
    if res.get("total", 0) == 0:
        return 0  # pessimistic: critic unreachable → treat as unsupported
    return 1 if res.get("supported") else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gate-kappa", type=float, default=0.7)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap pairs (0 = all, expected 200)")
    args = ap.parse_args()

    pairs = _load_pairs(args.pairs)
    if args.limit > 0:
        pairs = pairs[: args.limit]
    if not pairs:
        sys.stderr.write(f"[grounding-eval] no pairs in {args.pairs}\n")
        return 2

    if not gen_client._backend_active():
        sys.stderr.write(
            "[grounding-eval] RC_REASONER_BACKEND not set — abort\n"
        )
        return 2
    if not gen_client.health_ok():
        sys.stderr.write("[grounding-eval] gen sidecar /health fail — abort\n")
        return 2

    teacher = [int(p["label"]) for p in pairs]
    qwen: List[int] = []
    t0 = time.time()
    for i, p in enumerate(pairs):
        qwen.append(_qwen_label(p["claim"], p["hunk"]))
        if (i + 1) % 25 == 0:
            sys.stderr.write(
                f"[grounding-eval] {i+1}/{len(pairs)} ({time.time()-t0:.1f}s)\n"
            )

    kappa = _cohens_kappa(teacher, qwen)
    lo, hi = _bootstrap_ci(teacher, qwen)
    n = len(pairs)
    accuracy = sum(1 for x, y in zip(teacher, qwen) if x == y) / n

    result = {
        "n": n,
        "kappa": kappa,
        "kappa_ci95": [lo, hi],
        "accuracy": accuracy,
        "gate_kappa": args.gate_kappa,
        "gate_pass": kappa >= args.gate_kappa and lo >= (args.gate_kappa - 0.1),
        "elapsed_s": time.time() - t0,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    sys.stderr.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
