"""Build a PRM training corpus from iter-2 / iter-3 Claude sessions.

Audit 2026-06-01 §9. Mid-2026 PRM literature (AgentPRM arXiv:2511.08325,
Math-Shepherd arXiv:2312.08935) labels intermediate steps automatically
via outcome propagation. We adopt the simpler proxy: a step is +1 if its
session resolved (`meta.json.resolved==true`), -1 if a regression was
introduced, 0 otherwise.

Input layout (per session):
    <eval_root>/runs/<arm>/<task>/run-<N>/
        plan.md           — agent plan (required)
        meta.json         — outcome (resolved, regression_introduced, ...)
        diff_stats.json   — per-file added/removed line counts (optional)
        safety.json       — per-run safety signals (optional)

Output: one JSONL row per (plan_claim × diff-touched file) pair:
    {
      "id": "<task>_<arm>_<run>_<i>",
      "task_id": "...",
      "arm": "A" | "B",
      "plan_claim": "...",
      "diff_hunk": "<filename> +<added>/-<removed> lines",
      "step_label": -1 | 0 | 1,
      "outcome_label": 0 | 1,
      "source": "iter2-<datestamp>"
    }
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_DEFAULT_IN = "/Users/jakubsikora/evals/2026-05-06_205821_iter2-a-vanilla-vs-b-reasoning-core-fresh-v3"
_DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "eval", "calibrated", "prm_corpus.jsonl",
)
_MAX_PAIRS_PER_SESSION = 20
_CLAIM_REGEX = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$")
_SOURCE_LABEL = "iter2-2026-05-06"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _extract_claims(plan_text: str) -> List[str]:
    """Pull bullets / numbered items from the plan as plan claims."""
    claims: List[str] = []
    for line in plan_text.splitlines():
        m = _CLAIM_REGEX.match(line)
        if m:
            cleaned = m.group(1).strip()
            if cleaned and len(cleaned) > 4:
                claims.append(cleaned[:300])
    return claims


def _diff_files(diff_stats: Optional[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    """Return list of (filename, added, removed)."""
    if not isinstance(diff_stats, dict):
        return []
    out: List[Tuple[str, int, int]] = []
    files = diff_stats.get("files") or diff_stats.get("per_file") or []
    if isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            name = entry.get("path") or entry.get("file") or entry.get("name")
            if not isinstance(name, str):
                continue
            added = int(entry.get("added", entry.get("insertions", 0)) or 0)
            removed = int(entry.get("removed", entry.get("deletions", 0)) or 0)
            out.append((name, added, removed))
    elif isinstance(files, dict):
        for name, payload in files.items():
            if isinstance(payload, dict):
                added = int(payload.get("added", payload.get("insertions", 0)) or 0)
                removed = int(payload.get("removed", payload.get("deletions", 0)) or 0)
                out.append((name, added, removed))
    return out


def _step_label(
    *,
    meta: Dict[str, Any],
    file_name: str,
    safety: Optional[Dict[str, Any]],
) -> int:
    """Propagate the session outcome to per-file step labels."""
    if safety and safety.get("unsafe_command") is True:
        return -1
    if safety and safety.get("destructive_ops"):
        return -1
    if meta.get("regression_introduced") is True or meta.get("_regression_inferred") is True:
        regression_files = meta.get("regression_files") or []
        if isinstance(regression_files, list) and regression_files:
            return -1 if file_name in regression_files else 0
        return -1
    if meta.get("resolved") is True or meta.get("_resolved_inferred") is True:
        return 1
    return 0


def _iter_runs(eval_root: Path) -> Iterable[Tuple[str, str, str, Path]]:
    runs_root = eval_root / "runs"
    if not runs_root.is_dir():
        return
    for arm_dir in sorted(runs_root.iterdir()):
        if not arm_dir.is_dir():
            continue
        arm = arm_dir.name
        for task_dir in sorted(arm_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task = task_dir.name
            for run_dir in sorted(task_dir.iterdir()):
                if not run_dir.is_dir() or not run_dir.name.startswith("run-"):
                    continue
                yield arm, task, run_dir.name, run_dir


def _grade_outcome(eval_root: Path, arm: str, task: str, run_name: str) -> Tuple[int, int]:
    """Read judge grades for (arm, task, run_name); return (resolved, regression).

    Convention: correctness_determinism >= 4 → resolved; <= 2 → regression.
    Average across all grader-*.json files. Returns (0,0) when no grades.
    """
    grade_dir = eval_root / "grades" / arm / task / run_name
    if not grade_dir.is_dir():
        return 0, 0
    scores: List[float] = []
    for f in grade_dir.glob("grader-*.json"):
        d = _read_json(f)
        if not isinstance(d, dict):
            continue
        s = d.get("scores", {}).get("correctness_determinism")
        if isinstance(s, (int, float)):
            scores.append(float(s))
    if not scores:
        return 0, 0
    # Majority-vote: a session is "resolved" if a majority of judges
    # scored correctness_determinism >= 4; "regressed" if a majority
    # scored <= 2. Falls back to mean tie-breaker when judges split.
    high = sum(1 for s in scores if s >= 4)
    low = sum(1 for s in scores if s <= 2)
    if high > low:
        return 1, 0
    if low > high:
        return 0, 1
    mean = sum(scores) / len(scores)
    return (1 if mean >= 4.0 else 0), (1 if mean <= 2.0 else 0)


def build_rows(eval_root: Path, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    n_seen = 0
    n_skipped = 0
    for arm, task, run_name, run_dir in _iter_runs(eval_root):
        n_seen += 1
        plan_path = run_dir / "plan.md"
        meta_path = run_dir / "meta.json"
        if not plan_path.exists() or not meta_path.exists():
            n_skipped += 1
            continue
        try:
            plan_text = plan_path.read_text(encoding="utf-8")
        except OSError:
            n_skipped += 1
            continue
        meta = _read_json(meta_path)
        if not isinstance(meta, dict):
            n_skipped += 1
            continue
        diff_stats = _read_json(run_dir / "diff_stats.json")
        safety = _read_json(run_dir / "safety.json")
        claims = _extract_claims(plan_text)
        diff_files = _diff_files(diff_stats)
        # Fallback: iter-2 diff_stats stores plain filenames in "files" list
        # without per-file deltas. Synthesize (filename, 0, 0).
        if not diff_files and isinstance(diff_stats, dict):
            raw_files = diff_stats.get("files")
            if isinstance(raw_files, list):
                diff_files = [(f, 0, 0) for f in raw_files if isinstance(f, str)]
        if not claims or not diff_files:
            n_skipped += 1
            continue
        # Outcome label: prefer meta.resolved if present, else fall back to judge grades.
        if "resolved" in meta:
            outcome_label = 1 if meta.get("resolved") is True else 0
        else:
            resolved_flag, regression_flag = _grade_outcome(eval_root, arm, task, run_name)
            outcome_label = resolved_flag
            if regression_flag:
                meta["_regression_inferred"] = True
            elif resolved_flag:
                meta["_resolved_inferred"] = True
        task_id = meta.get("task_id") or meta.get("task") or task
        run_arm = meta.get("arm") or meta.get("setup") or arm
        n_pairs = 0
        for i, claim in enumerate(claims):
            for fname, added, removed in diff_files:
                if n_pairs >= _MAX_PAIRS_PER_SESSION:
                    break
                rows.append({
                    "id": f"{task_id}_{run_arm}_{run_name}_{n_pairs}",
                    "task_id": task_id,
                    "arm": run_arm,
                    "plan_claim": claim,
                    "diff_hunk": f"{fname} +{added}/-{removed} lines",
                    "step_label": _step_label(meta=meta, file_name=fname, safety=safety),
                    "outcome_label": outcome_label,
                    "source": _SOURCE_LABEL,
                })
                n_pairs += 1
                if limit is not None and len(rows) >= limit:
                    sys.stderr.write(
                        f"build_prm_corpus: hit --limit {limit}; stopping early\n"
                    )
                    return rows
            if n_pairs >= _MAX_PAIRS_PER_SESSION:
                break
    sys.stderr.write(
        f"build_prm_corpus: sessions seen={n_seen} skipped={n_skipped} rows={len(rows)}\n"
    )
    label_counts: Dict[int, int] = {-1: 0, 0: 0, 1: 0}
    for r in rows:
        label_counts[r["step_label"]] = label_counts.get(r["step_label"], 0) + 1
    sys.stderr.write(f"build_prm_corpus: label distribution {label_counts}\n")
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="eval_root", default=_DEFAULT_IN,
                   help="path to eval folder containing runs/<arm>/<task>/run-N/")
    p.add_argument("--out", default=_DEFAULT_OUT,
                   help="output jsonl path")
    p.add_argument("--limit", type=int, default=None,
                   help="cap the number of rows (debugging)")
    args = p.parse_args(argv)
    eval_root = Path(args.eval_root)
    if not eval_root.is_dir():
        sys.stderr.write(
            f"build_prm_corpus: --in {eval_root} does not exist; nothing to do.\n"
        )
        return 0
    rows = build_rows(eval_root, limit=args.limit)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    sys.stderr.write(f"build_prm_corpus: wrote {len(rows)} rows to {out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
