#!/usr/bin/env python3
"""Regenerate the SWE-bench Verified Python subset.

Pulls ``princeton-nlp/SWE-bench_Verified`` from Hugging Face, applies the
filters documented in EVAL_DESIGN.md section 2 (Python-only, easy/medium
difficulty, no Docker repos, gold patch <= 5 files, no .c/.rs/.go),
stratifies by ``n_test_files_touched`` into [1], [2-5], [6+], samples
~34/33/33 with ``random.seed(42)``, and writes the output JSON.

Usage:
    python3 eval/datasets/refresh_subset.py [--n 100] [--dry-run]

Offline / Cato-blocked envs: the loader will fail; run
``HF_DATASETS_OFFLINE=1 python3 eval/datasets/refresh_subset.py --dry-run``
to print a diagnostic without trying the network.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import sys
from typing import Any

# Repos that require Docker to install dependencies (excluded per the spec).
DOCKER_REQUIRED: set[str] = {
    "matplotlib/matplotlib",
    "scikit-learn/scikit-learn",
    "pylint-dev/pylint",
    "pytest-dev/pytest",
    "pydata/xarray",
    "astropy/astropy",
}

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "swe_bench_verified_python_subset.json",
)


def _is_python_only_patch(patch_text: str) -> bool:
    """Heuristic: only files ending in .py are touched, no .c/.rs/.go."""
    if not patch_text:
        return False
    file_lines = [ln for ln in patch_text.splitlines() if ln.startswith("diff --git")]
    if not file_lines:
        return False
    for ln in file_lines:
        # diff --git a/path/foo.py b/path/foo.py
        parts = ln.split()
        if len(parts) < 4:
            return False
        path = parts[-1]
        if not path.endswith(".py"):
            return False
    return True


def _patch_file_count(patch_text: str) -> int:
    return sum(1 for ln in (patch_text or "").splitlines() if ln.startswith("diff --git"))


def _patch_test_file_count(patch_text: str) -> int:
    n = 0
    for ln in (patch_text or "").splitlines():
        if ln.startswith("diff --git") and ("/test" in ln or "tests/" in ln):
            n += 1
    return n


def _stratify(rows: list[dict[str, Any]], target_total: int, seed: int) -> list[dict[str, Any]]:
    """Sample ~target_total/3 from each of the 3 strata."""
    s1 = [r for r in rows if r["n_test_files_touched"] == 1]
    s2_5 = [r for r in rows if 2 <= r["n_test_files_touched"] <= 5]
    s6plus = [r for r in rows if r["n_test_files_touched"] >= 6]

    rng = random.Random(seed)
    rng.shuffle(s1)
    rng.shuffle(s2_5)
    rng.shuffle(s6plus)

    base = target_total // 3
    sizes = [base, base, target_total - 2 * base]
    sampled = (s1[:sizes[0]] + s2_5[:sizes[1]] + s6plus[:sizes[2]])
    return sampled


def _filter_row(row: dict[str, Any]) -> bool:
    repo = row.get("repo", "")
    if repo in DOCKER_REQUIRED:
        return False
    diff = row.get("patch", "") or ""
    if not _is_python_only_patch(diff):
        return False
    if _patch_file_count(diff) > 5:
        return False
    diff_lower = diff.lower()
    if any(ext in diff_lower for ext in (".c\n", ".rs\n", ".go\n")):
        return False
    difficulty = (row.get("difficulty") or "").lower()
    if "<15" not in difficulty and "15 min" not in difficulty:
        return False
    return True


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=100, help="target sample size (default 100)")
    p.add_argument("--dry-run", action="store_true", help="print pass/fail counts only")
    p.add_argument("--out", default=DEFAULT_OUTPUT)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> int:  # pragma: no cover -- exercised manually with HF reachable
    args = _parse_args()
    try:
        from datasets import load_dataset  # type: ignore
    except Exception as exc:
        print(f"ERROR: datasets library not installed ({exc}); cannot refresh.", file=sys.stderr)
        print("Install: pip install datasets==2.20.0", file=sys.stderr)
        return 1

    print("Loading princeton-nlp/SWE-bench_Verified ...", file=sys.stderr)
    try:
        ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    except Exception as exc:
        print(f"ERROR: load_dataset failed ({exc}).", file=sys.stderr)
        print("If your env is offline, ship the existing JSON unchanged.", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    rejected = {"docker": 0, "non_python": 0, "too_many_files": 0, "wrong_difficulty": 0}
    for row in ds:
        if row.get("repo", "") in DOCKER_REQUIRED:
            rejected["docker"] += 1
            continue
        diff = row.get("patch") or ""
        if not _is_python_only_patch(diff):
            rejected["non_python"] += 1
            continue
        if _patch_file_count(diff) > 5:
            rejected["too_many_files"] += 1
            continue
        difficulty = (row.get("difficulty") or "").lower()
        if "<15" not in difficulty and "15 min" not in difficulty:
            rejected["wrong_difficulty"] += 1
            continue
        rows.append({
            "instance_id": row.get("instance_id"),
            "repo": row.get("repo"),
            "base_commit": row.get("base_commit"),
            "difficulty": row.get("difficulty"),
            "n_test_files_touched": _patch_test_file_count(diff),
            "fail_to_pass": list(row.get("FAIL_TO_PASS") or []),
            "pass_to_pass": list(row.get("PASS_TO_PASS") or []),
        })

    print(f"filtered {len(rows)} candidates; rejected={rejected}", file=sys.stderr)
    if args.dry_run:
        return 0

    sampled = _stratify(rows, args.n, args.seed)
    out = {
        "schema_version": 1,
        "generated_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "princeton-nlp/SWE-bench_Verified",
        "source_revision": os.environ.get("HF_DATASET_REVISION", "main"),
        "n": len(sampled),
        "seed": args.seed,
        "strata": {
            "1": sum(1 for r in sampled if r["n_test_files_touched"] == 1),
            "2-5": sum(1 for r in sampled if 2 <= r["n_test_files_touched"] <= 5),
            "6+": sum(1 for r in sampled if r["n_test_files_touched"] >= 6),
        },
        "tasks": sampled,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"wrote {len(sampled)} tasks to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
