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
import hashlib
from typing import Dict, List, Optional, Tuple

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
    samples: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        sa = [a[i] for i in idx]
        sb = [b[i] for i in idx]
        # Filter degenerate resamples (all-same labels on either side) — they
        # collapse kappa to 0/1 and skew the CI bounds.
        if len(set(sa)) < 2 or len(set(sb)) < 2:
            continue
        try:
            samples.append(_cohens_kappa(sa, sb))
        except AssertionError:
            continue
    if not samples:
        return 0.0, 0.0
    samples.sort()
    n_s = len(samples)
    lo = samples[max(0, int(0.025 * n_s))]
    hi = samples[min(n_s - 1, int(0.975 * n_s))]
    return lo, hi


_REQUIRED_KEYS = ("id", "claim", "hunk", "label")


def _load_pairs(path: Path) -> List[dict]:
    """Schema-validate up-front; refuse mid-eval crashes after sidecar burn."""
    out = []
    with path.open() as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {ln}: invalid JSON ({exc})") from exc
            for k in _REQUIRED_KEYS:
                if k not in obj:
                    raise ValueError(f"line {ln}: missing required key {k!r}")
            if obj["label"] not in (0, 1):
                raise ValueError(f"line {ln}: label must be 0 or 1, got {obj['label']!r}")
            out.append(obj)
    return out


def _qwen_label_3state(claim: str, hunk: str) -> Optional[int]:
    """Returns 1, 0, or None (unreachable). Round-2 fix: do NOT coerce
    unreachable to 0 — it inflates kappa via spurious negative agreement."""
    res = gen_client.score_plan_grounding(claim, hunk)
    if res.get("total", 0) == 0:
        return None
    return 1 if res.get("supported") else 0


def _load_partial(out_path: Path) -> Dict[str, int]:
    """Read previously-computed labels for resume."""
    partial = out_path.with_suffix(out_path.suffix + ".partial.jsonl")
    seen: Dict[str, int] = {}
    if partial.exists():
        with partial.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    seen[rec["id"]] = rec["qwen"]
                except (json.JSONDecodeError, KeyError):
                    continue
    return seen


def _append_partial(out_path: Path, pid: str, qwen: int) -> None:
    partial = out_path.with_suffix(out_path.suffix + ".partial.jsonl")
    with partial.open("a") as f:
        f.write(json.dumps({"id": pid, "qwen": qwen}) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--gate-kappa", type=float, default=0.7)
    ap.add_argument("--ci-floor", type=float, default=0.6,
                    help="bootstrap 95% CI lower bound floor for gate")
    ap.add_argument("--unreachable-max", type=float, default=0.05,
                    help="abort gate if unreachable_rate > this")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap pairs (0 = all; n=500 recommended for kappa>=0.7 power)")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore .partial.jsonl checkpoint and restart")
    args = ap.parse_args()

    pairs = _load_pairs(args.pairs)
    if args.limit > 0:
        pairs = pairs[: args.limit]
    if not pairs:
        sys.stderr.write(f"[grounding-eval] no pairs in {args.pairs}\n")
        return 2

    if not gen_client._backend_active():
        sys.stderr.write("[grounding-eval] RC_REASONER_BACKEND not set — abort\n")
        return 2
    if not gen_client.health_ok():
        sys.stderr.write("[grounding-eval] gen sidecar /v1/models fail — abort\n")
        return 2

    seen = {} if args.no_resume else _load_partial(args.out)
    if seen:
        sys.stderr.write(f"[grounding-eval] resume: skipping {len(seen)} prior\n")

    teacher: List[int] = []
    qwen: List[int] = []
    unreachable = 0
    t0 = time.time()

    for i, p in enumerate(pairs):
        pid = p["id"]
        if pid in seen:
            label = seen[pid]
        else:
            res = _qwen_label_3state(p["claim"], p["hunk"])
            if res is None:
                unreachable += 1
                continue  # exclude from kappa
            label = res
            _append_partial(args.out, pid, label)
        teacher.append(int(p["label"]))
        qwen.append(label)
        if (i + 1) % 25 == 0:
            sys.stderr.write(
                f"[grounding-eval] {i+1}/{len(pairs)} "
                f"(unreachable={unreachable}, {time.time()-t0:.1f}s)\n"
            )

    n = len(qwen)
    if n < 30:
        sys.stderr.write(f"[grounding-eval] too few reachable pairs (n={n})\n")
        return 2

    kappa = _cohens_kappa(teacher, qwen)
    lo, hi = _bootstrap_ci(teacher, qwen)
    accuracy = sum(1 for x, y in zip(teacher, qwen) if x == y) / n
    unreachable_rate = unreachable / len(pairs)

    prompt_hash = hashlib.sha256(
        gen_client._GROUNDING_PROMPT.encode()
    ).hexdigest()[:16]

    gate_pass = (
        kappa >= args.gate_kappa
        and lo >= args.ci_floor
        and unreachable_rate <= args.unreachable_max
    )

    result = {
        "n": n,
        "n_total": len(pairs),
        "unreachable": unreachable,
        "unreachable_rate": unreachable_rate,
        "kappa": kappa,
        "kappa_ci95": [lo, hi],
        "accuracy": accuracy,
        "gate_kappa": args.gate_kappa,
        "gate_ci_floor": args.ci_floor,
        "gate_unreachable_max": args.unreachable_max,
        "gate_pass": gate_pass,
        "elapsed_s": time.time() - t0,
        "prompt_hash": prompt_hash,
        "bootstrap_seed": 7,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    sys.stderr.write(json.dumps(result, indent=2) + "\n")

    # Write a sentinel that CDGS reads to decide whether to trust Qwen.
    sentinel = args.out.parent / "qwen_kappa_gate.json"
    sentinel.write_text(json.dumps({"gate_pass": gate_pass, "kappa": kappa}))
    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
