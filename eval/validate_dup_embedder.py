#!/usr/bin/env python3
"""Embedder fitness for the near-duplicate oracle (offline).

Falsifiable check that the pinned code embedder places genuine duplicates far
closer than unrelated functions -- the premise the oracle's Stage-1 cosine
shortlist relies on. Reuses the frozen date-fns fixture vectors
(tests/fixtures/dup_oracle/), so it needs no model and is deterministic; the
known duplicate there is ``cleanEscapedString`` (copied across
format/lightFormat/parse).

Pass gate: mean(duplicate-pair cosine) exceeds mean(unrelated-pair cosine) by
>= --sigma-threshold standard deviations of the unrelated distribution.

Usage:
    python -m eval.validate_dup_embedder [--sigma-threshold 3.0]
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

import numpy as np

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "dup_oracle"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigma-threshold", type=float, default=3.0)
    args = ap.parse_args()

    records = json.loads((FIXTURE / "date_fns_functions.json").read_text())
    vecs = np.load(FIXTURE / "date_fns_vectors.npy")  # L2-normalised rows

    dup_idx = [i for i, r in enumerate(records) if r["name"] == "cleanEscapedString"]
    if len(dup_idx) < 2:
        print("fixture missing the cleanEscapedString duplicate cluster", file=sys.stderr)
        return 2
    dup_set = set(dup_idx)

    dup_sims = [float(vecs[i] @ vecs[j]) for i, j in itertools.combinations(dup_idx, 2)]
    unrelated_sims = [
        float(vecs[i] @ vecs[j])
        for i in dup_idx
        for j in range(len(records))
        if j not in dup_set
    ]

    dup_mean = statistics.mean(dup_sims)
    unrelated_mean = statistics.mean(unrelated_sims)
    unrelated_std = statistics.pstdev(unrelated_sims)
    sep = (dup_mean - unrelated_mean) / unrelated_std if unrelated_std else float("inf")

    out = {
        "n_functions": len(records),
        "duplicate": "cleanEscapedString",
        "dup_pair_cosine_mean": round(dup_mean, 4),
        "unrelated_cosine_mean": round(unrelated_mean, 4),
        "unrelated_cosine_std": round(unrelated_std, 4),
        "separation_sigma": round(sep, 2),
        "sigma_threshold": args.sigma_threshold,
        "embedder_fit": sep >= args.sigma_threshold,
    }
    print(json.dumps(out, indent=2))
    return 0 if out["embedder_fit"] else 1


if __name__ == "__main__":
    sys.exit(main())
