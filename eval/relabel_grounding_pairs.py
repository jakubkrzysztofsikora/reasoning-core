"""Judge-relabel grounding_pairs.jsonl with a strong Scaleway model.

Reads eval/datasets/grounding_pairs.jsonl, asks devstral-2-123b-instruct-2512
the same rubric prompt used by gen_client.score_plan_grounding for each
(claim, hunk) pair, and emits a "v2" file containing only pairs where the
teacher label and the judge label agree (high-confidence subset).

Usage:
    python -m eval.relabel_grounding_pairs \
        --in eval/datasets/grounding_pairs.jsonl \
        --out eval/datasets/grounding_pairs_v2.jsonl \
        [--limit N]

Resume-safe: stores per-pair judge labels to <out>.judge.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def _load_pairs(path: Path) -> list:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _load_judge_cache(path: Path) -> Dict[str, Optional[int]]:
    cache: Dict[str, Optional[int]] = {}
    if not path.exists():
        return cache
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cache[rec["id"]] = rec.get("judge")
    return cache


def _append_cache(path: Path, pid: str, judge: Optional[int]) -> None:
    with path.open("a") as f:
        f.write(json.dumps({"id": pid, "judge": judge}) + "\n")


def _setup_env() -> None:
    os.environ["RC_REASONER_BACKEND"] = "remote"
    os.environ["RC_GEN_URL"] = "https://api.scaleway.ai/v1/chat/completions"
    if not os.environ.get("RC_GEN_API_KEY"):
        # Round-3 fix (AH-H3): fall through to env-only auth in CI where
        # `scw` is not installed. Operator must export RC_GEN_API_KEY or
        # SCALEWAY_API_KEY in that case.
        try:
            key = subprocess.check_output(
                ["scw", "config", "get", "secret-key", "--profile", "circit"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            if key:
                os.environ["RC_GEN_API_KEY"] = key
        except (FileNotFoundError, subprocess.CalledProcessError):
            scaleway = os.environ.get("SCALEWAY_API_KEY")
            if scaleway:
                os.environ["RC_GEN_API_KEY"] = scaleway
            else:
                raise SystemExit(
                    "[relabel] no RC_GEN_API_KEY / SCALEWAY_API_KEY in env, "
                    "and `scw` CLI not available. Set one and retry."
                )
    os.environ["RC_GEN_MODEL"] = "devstral-2-123b-instruct-2512"
    os.environ["RC_GEN_BUDGET_MS"] = os.environ.get("RC_GEN_BUDGET_MS", "25000")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    _setup_env()
    import gen_client  # noqa: E402

    if not gen_client.health_ok():
        sys.stderr.write("[relabel] judge endpoint health check failed\n")
        return 2

    pairs = _load_pairs(args.inp)
    if args.limit > 0:
        pairs = pairs[: args.limit]

    cache_path = args.out.with_suffix(args.out.suffix + ".judge.jsonl")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_judge_cache(cache_path)
    sys.stderr.write(f"[relabel] {len(pairs)} pairs, {len(cache)} cached\n")

    t0 = time.time()
    for i, p in enumerate(pairs):
        pid = p["id"]
        if pid in cache:
            continue
        res = gen_client.score_plan_grounding(p["claim"], p["hunk"])
        judge: Optional[int]
        if res.get("total", 0) == 0:
            judge = None
        else:
            judge = 1 if res.get("supported") else 0
        cache[pid] = judge
        _append_cache(cache_path, pid, judge)
        if (i + 1) % 10 == 0:
            sys.stderr.write(
                f"[relabel] {i+1}/{len(pairs)} ({time.time()-t0:.1f}s)\n"
            )

    # Build v2: keep pairs where teacher_label == judge_label.
    kept = []
    by_cat = {"pos": [0, 0], "shuf": [0, 0], "hard": [0, 0]}  # [agree, total]
    unreachable = 0
    for p in pairs:
        cat = (
            "pos" if p["id"].startswith("pos_")
            else "shuf" if p["id"].startswith("shuf_neg_")
            else "hard" if p["id"].startswith("hard_neg_")
            else "other"
        )
        j = cache.get(p["id"])
        if j is None:
            unreachable += 1
            continue
        if cat in by_cat:
            by_cat[cat][1] += 1
        if j == int(p["label"]):
            kept.append(p)
            if cat in by_cat:
                by_cat[cat][0] += 1

    # Atomic write (round-3 round-2 senior-dev H1): SIGINT mid-write of the
    # final v2 jsonl would corrupt the file the κ eval consumes. The judge
    # cache is append-only and resumable, but the v2 jsonl is regenerated
    # whole. Write to .tmp then os.replace for crash-safety.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = args.out.with_suffix(args.out.suffix + f".tmp.{os.getpid()}")
    try:
        with tmp_out.open("w") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_out, args.out)
    except Exception:
        try:
            tmp_out.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    summary = {
        "pre_n": len(pairs),
        "post_n": len(kept),
        "unreachable": unreachable,
        "agreement_by_category": {
            k: {"agree": v[0], "total": v[1],
                "rate": (v[0] / v[1]) if v[1] else None}
            for k, v in by_cat.items()
        },
        "elapsed_s": time.time() - t0,
        "judge_model": os.environ["RC_GEN_MODEL"],
    }
    sys.stderr.write(json.dumps(summary, indent=2) + "\n")
    # Same atomic-write pattern for the summary sidecar.
    summary_path = args.out.with_suffix(args.out.suffix + ".summary.json")
    tmp_summary = summary_path.with_suffix(summary_path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp_summary.write_text(json.dumps(summary, indent=2))
        os.replace(tmp_summary, summary_path)
    except Exception:
        try:
            tmp_summary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
