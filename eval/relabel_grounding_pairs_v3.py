"""Cross-family judge-relabel for grounding_pairs.jsonl (Phase 3.5 v3).

Forks `eval/relabel_grounding_pairs.py` to support multiple judges from
different model families and a 2-of-3 majority filter — addresses kin-
judge contamination flagged by LLM-sci review (devstral judge + qwen-coder
test = same coder family).

Default judges (cross-family):
- devstral-2-123b-instruct-2512        (coder, kept from v2)
- llama-3.3-70b-instruct               (Meta general)
- mistral-small-3.2-24b-instruct-2506  (Mistral general)

Per-judge resumable cache: <out>.judge.<sanitized_model_id>.jsonl
Cost meter: aborts at $20 hard, warns at $18 soft.

Usage:
    # Pilot (50 ids from pilot50_ids.txt) for independence test:
    python -m eval.relabel_grounding_pairs_v3 \\
        --in eval/datasets/grounding_pairs.jsonl \\
        --out eval/datasets/grounding_pairs_v3.jsonl \\
        --mode pilot

    # Full (200 pairs, applies 2-of-3 majority filter):
    python -m eval.relabel_grounding_pairs_v3 \\
        --in eval/datasets/grounding_pairs.jsonl \\
        --out eval/datasets/grounding_pairs_v3.jsonl \\
        --mode full

    # Independence-only (loads pilot caches, computes pairwise kappa):
    python -m eval.relabel_grounding_pairs_v3 \\
        --in eval/datasets/grounding_pairs.jsonl \\
        --out eval/datasets/grounding_pairs_v3.jsonl \\
        --check-independence
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_JUDGES = (
    "devstral-2-123b-instruct-2512,"
    "llama-3.3-70b-instruct,"
    "mistral-small-3.2-24b-instruct-2506"
)

PILOT_IDS_PATH = REPO_ROOT / "eval" / "datasets" / "grounding_pilot50_ids.txt"

# Rough per-call cost estimate (USD). Scaleway pricing varies per model;
# ~$0.02/call is conservative-high blended estimate (1-2k input + ~50 output).
COST_PER_CALL_USD = 0.02
COST_HARD_ABORT_USD = 20.0
COST_SOFT_WARN_USD = 18.0


def _sanitize_model_id(m: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", m)


def _load_pairs(path: Path) -> List[dict]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _load_pilot_indices() -> List[int]:
    if not PILOT_IDS_PATH.exists():
        raise SystemExit(f"[v3] missing {PILOT_IDS_PATH}; run pre-flight ID gen")
    out: List[int] = []
    for line in PILOT_IDS_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(int(line))
    return out


def _judge_cache_path(out_path: Path, model_id: str) -> Path:
    return out_path.with_suffix(
        out_path.suffix + f".judge.{_sanitize_model_id(model_id)}.jsonl"
    )


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"id": pid, "judge": judge}) + "\n")


def _setup_env() -> None:
    os.environ["RC_REASONER_BACKEND"] = "remote"
    os.environ["RC_GEN_URL"] = "https://api.scaleway.ai/v1/chat/completions"
    if not os.environ.get("RC_GEN_API_KEY"):
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
                    "[v3] no RC_GEN_API_KEY/SCALEWAY_API_KEY in env, and `scw` "
                    "CLI not available. Set one and retry."
                )
    os.environ["RC_GEN_BUDGET_MS"] = os.environ.get("RC_GEN_BUDGET_MS", "25000")


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


def _select_pair_indices(mode: str, total: int) -> List[int]:
    if mode == "pilot":
        return _load_pilot_indices()
    if mode == "full":
        return list(range(total))
    raise SystemExit(f"[v3] unknown mode {mode!r}")


def _label_pair(pair: dict) -> Optional[int]:
    import gen_client  # noqa: WPS433  (re-import safe; module-level cached)
    res = gen_client.score_plan_grounding(pair["claim"], pair["hunk"])
    if res.get("total", 0) == 0:
        return None
    return 1 if res.get("supported") else 0


def _run_judge_pass(
    judges: List[str],
    pairs: List[dict],
    indices: List[int],
    out_path: Path,
    cost_state: Dict[str, float],
) -> Dict[str, Dict[str, Optional[int]]]:
    """Label `indices` of `pairs` with each judge in `judges`. Returns
    {model_id: {pid: judge_label}} caches (post-run, includes prior cache)."""
    import gen_client  # noqa: F401  ensure module imported
    caches: Dict[str, Dict[str, Optional[int]]] = {}
    for jm in judges:
        cache_path = _judge_cache_path(out_path, jm)
        cache = _load_judge_cache(cache_path)
        caches[jm] = cache
        os.environ["RC_GEN_MODEL"] = jm
        sys.stderr.write(
            f"[v3] judge={jm} cached={len(cache)} "
            f"target_indices={len(indices)}\n"
        )
        t0 = time.time()
        for n_done, idx in enumerate(indices):
            p = pairs[idx]
            pid = p["id"]
            if pid in cache:
                continue
            # Cost meter
            cost_state["calls"] += 1
            cost_state["spent"] = cost_state["calls"] * COST_PER_CALL_USD
            if cost_state["spent"] >= COST_HARD_ABORT_USD:
                sys.stderr.write(
                    f"[v3] HARD ABORT: cost ${cost_state['spent']:.2f} "
                    f">= ${COST_HARD_ABORT_USD:.2f}\n"
                )
                raise SystemExit(3)
            if (
                cost_state["spent"] >= COST_SOFT_WARN_USD
                and not cost_state.get("warned")
            ):
                sys.stderr.write(
                    f"[v3] SOFT WARN: cost ${cost_state['spent']:.2f} "
                    f">= ${COST_SOFT_WARN_USD:.2f}\n"
                )
                cost_state["warned"] = 1.0
            label = _label_pair(p)
            cache[pid] = label
            _append_cache(cache_path, pid, label)
            if (n_done + 1) % 25 == 0:
                sys.stderr.write(
                    f"[v3]   {jm}: {n_done+1}/{len(indices)} "
                    f"({time.time()-t0:.1f}s, $={cost_state['spent']:.2f})\n"
                )
    return caches


def _majority_label(labels: List[Optional[int]]) -> Tuple[Optional[int], int, int]:
    """Return (majority_label, agreeing_count, reachable_count). majority_label
    is None if <2 reachable judges OR no >=2 agreement."""
    reachable = [x for x in labels if x is not None]
    if len(reachable) < 2:
        return None, 0, len(reachable)
    counts: Dict[int, int] = {}
    for x in reachable:
        counts[x] = counts.get(x, 0) + 1
    top_label = max(counts, key=counts.get)
    if counts[top_label] >= 2:
        return top_label, counts[top_label], len(reachable)
    return None, 0, len(reachable)


def _emit_v3(
    pairs: List[dict],
    caches: Dict[str, Dict[str, Optional[int]]],
    out_path: Path,
    judges: List[str],
    *,
    require_teacher_agreement: bool,
) -> dict:
    kept: List[dict] = []
    by_cat = {"pos": [0, 0], "shuf": [0, 0], "hard": [0, 0]}
    unreachable = 0
    no_majority = 0
    teacher_disagree = 0
    for p in pairs:
        cat = (
            "pos" if p["id"].startswith("pos_")
            else "shuf" if p["id"].startswith("shuf_neg_")
            else "hard" if p["id"].startswith("hard_neg_")
            else "other"
        )
        labels = [caches[jm].get(p["id"]) for jm in judges]
        if all(x is None for x in labels):
            unreachable += 1
            continue
        maj, agree_n, reach_n = _majority_label(labels)
        if maj is None:
            no_majority += 1
            continue
        if cat in by_cat:
            by_cat[cat][1] += 1
        if require_teacher_agreement and maj != int(p["label"]):
            teacher_disagree += 1
            continue
        kept.append(p)
        if cat in by_cat:
            by_cat[cat][0] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(out_path.suffix + f".tmp.{os.getpid()}")
    try:
        with tmp_out.open("w") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_out, out_path)
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
        "no_majority": no_majority,
        "teacher_disagree": teacher_disagree,
        "require_teacher_agreement": require_teacher_agreement,
        "judges": judges,
        "agreement_by_category": {
            k: {"agree": v[0], "total": v[1],
                "rate": (v[0] / v[1]) if v[1] else None}
            for k, v in by_cat.items()
        },
    }
    summary_path = out_path.with_suffix(out_path.suffix + ".summary.json")
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
    sys.stderr.write(json.dumps(summary, indent=2) + "\n")
    return summary


def _check_independence(
    judges: List[str],
    pairs: List[dict],
    indices: List[int],
    out_path: Path,
) -> dict:
    """Compute pairwise Cohen's kappa over the pilot set; gate `max < 0.7`."""
    caches = {jm: _load_judge_cache(_judge_cache_path(out_path, jm)) for jm in judges}
    pilot_pids = [pairs[i]["id"] for i in indices]
    # Only pids where ALL 3 judges produced a non-None label.
    aligned: Dict[str, List[int]] = {jm: [] for jm in judges}
    for pid in pilot_pids:
        labels = [caches[jm].get(pid) for jm in judges]
        if any(x is None for x in labels):
            continue
        for jm, x in zip(judges, labels):
            aligned[jm].append(int(x))
    n = len(next(iter(aligned.values())))
    pairwise: Dict[str, float] = {}
    if n >= 2:
        a, b, c = judges
        pairwise[f"{a}__{b}"] = _cohens_kappa(aligned[a], aligned[b])
        pairwise[f"{a}__{c}"] = _cohens_kappa(aligned[a], aligned[c])
        pairwise[f"{b}__{c}"] = _cohens_kappa(aligned[b], aligned[c])
    max_k = max(pairwise.values()) if pairwise else 1.0
    gate_pass = max_k < 0.7
    payload = {
        "judges": judges,
        "n_aligned": n,
        "n_pilot": len(indices),
        "pairwise_kappa": pairwise,
        "max": max_k,
        "gate_pass": gate_pass,
        "gate_threshold": 0.7,
    }
    runs_dir = REPO_ROOT / "eval" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_json = runs_dir / "judge_independence_pilot_20260506.json"
    out_json.write_text(json.dumps(payload, indent=2))
    sys.stderr.write(json.dumps(payload, indent=2) + "\n")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--judges", default=DEFAULT_JUDGES,
        help="CSV of judge model ids (default = cross-family 3-judge set)"
    )
    ap.add_argument("--mode", choices=["pilot", "full"], default="pilot")
    ap.add_argument(
        "--check-independence", action="store_true",
        help="Skip labeling; load pilot caches and emit pairwise kappa report.",
    )
    ap.add_argument(
        "--no-teacher-agreement", action="store_true",
        help="Fallback: keep pairs based on judge majority alone "
             "(skip teacher-agreement filter).",
    )
    args = ap.parse_args()

    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    if len(judges) < 2:
        raise SystemExit("[v3] need >= 2 judges")
    pairs = _load_pairs(args.inp)
    pilot_idx = _load_pilot_indices()

    if args.check_independence:
        _check_independence(judges, pairs, pilot_idx, args.out)
        return 0

    _setup_env()
    import gen_client  # noqa: E402
    if not gen_client.health_ok():
        sys.stderr.write("[v3] judge endpoint health check failed\n")
        return 2

    indices = _select_pair_indices(args.mode, len(pairs))
    cost_state: Dict[str, float] = {"calls": 0, "spent": 0.0}
    caches = _run_judge_pass(judges, pairs, indices, args.out, cost_state)

    if args.mode == "pilot":
        sys.stderr.write(
            f"[v3] pilot done; spent~${cost_state['spent']:.2f} "
            f"({cost_state['calls']} calls). Run --check-independence next.\n"
        )
        return 0

    # full mode: emit v3 dataset with 2-of-3 majority filter.
    summary = _emit_v3(
        pairs, caches, args.out, judges,
        require_teacher_agreement=not args.no_teacher_agreement,
    )
    sys.stderr.write(
        f"[v3] full done; spent~${cost_state['spent']:.2f} "
        f"({cost_state['calls']} calls). post_n={summary['post_n']}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
