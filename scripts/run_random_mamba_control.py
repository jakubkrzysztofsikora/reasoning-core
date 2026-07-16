#!/usr/bin/env python3
"""Run random-mamba control against real embedder for falsifiability.

Falsifiability test — if random-mamba flags regressions at a similar rate
to the real SSM embedder, the SSM signal is indistinguishable from noise.

Loads a small corpus of (before, after) pairs from stdin (JSONL), scores
each pair with both the real embedder and random-mamba, and prints a
comparison table.

Usage:
    python3 scripts/run_random_mamba_control.py < fixture_pairs.jsonl

Input format (one JSON object per line):
    {"path": "foo.py", "before": "...", "after": "..."}

Output:
    JSON object with per-embedder regression rates and per-pair deltas.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "hooks"))


def _resolve_embedder(name: str) -> str:
    """Return the embedder name as-is; codestral-mamba now uses fp16 HF."""
    return name


def score_one(
    path: str,
    before_src: str,
    after_src: str,
    *,
    embedder: str,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Score one pair with the given embedder. Returns report dict or error."""
    os.environ["RC_EMBEDDER"] = embedder
    # Clear any cached backbone so the new embedder loads fresh.
    try:
        from ssm_backbone import clear_backbone_cache  # type: ignore
        clear_backbone_cache()
    except (ImportError, AttributeError):
        pass
    try:
        from s2_core import score_change  # type: ignore
        report = score_change(path, before_src, after_src, session_id=session_id)
        return {
            "regression_detected": report.regression_detected,
            "ais": report.architectural_impact_score,
            "coherence_delta": report.coherence_delta,
            "risk_vector": report.risk_vector,
            "fired_conditions": report.fired_conditions,
            "fired_dims": report.fired_dims,
            "fired_margins": report.fired_margins,
            "file_kind": report.file_kind,
        }
    except Exception as exc:
        return {"error": str(exc), "type": type(exc).__name__}


def run_control(pairs: list[dict[str, str]]) -> dict[str, Any]:
    """Score all pairs with both real and random embedder."""
    real_backend = _resolve_embedder(
        os.environ.get("RC_EMBEDDER", "mamba-130m")
    )
    results: dict[str, Any] = {
        "real_backend": real_backend,
        "n_pairs": len(pairs),
        "real": [],
        "random": [],
    }
    for i, pair in enumerate(pairs):
        path = pair.get("path", f"pair_{i}.py")
        before = pair.get("before", "")
        after = pair.get("after", "")
        t0 = time.time()
        real_r = score_one(path, before, after, embedder=real_backend)
        real_ms = int((time.time() - t0) * 1000)
        t0 = time.time()
        rand_r = score_one(path, before, after, embedder="random-mamba")
        rand_ms = int((time.time() - t0) * 1000)
        results["real"].append({**real_r, "latency_ms": real_ms})
        results["random"].append({**rand_r, "latency_ms": rand_ms})
        sys.stderr.write(
            f"[{i + 1}/{len(pairs)}] {path}: "
            f"real={real_r.get('regression_detected', '?')} ({real_ms}ms)  "
            f"random={rand_r.get('regression_detected', '?')} ({rand_ms}ms)\n"
        )
    return results


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    """Compute summary stats."""
    real_flags = sum(
        1 for r in results["real"]
        if r.get("regression_detected") is True
    )
    rand_flags = sum(
        1 for r in results["random"]
        if r.get("regression_detected") is True
    )
    real_errors = sum(1 for r in results["real"] if "error" in r)
    rand_errors = sum(1 for r in results["random"] if "error" in r)
    n = results["n_pairs"]
    real_rate = real_flags / max(1, n - real_errors) if n > real_errors else 0
    rand_rate = rand_flags / max(1, n - rand_errors) if n > rand_errors else 0
    # If random-mamba flags nothing, the SSM signal is non-null.
    # If rates are similar, the SSM signal is indistinguishable from noise.
    is_signal_non_null = rand_rate < real_rate * 0.5
    return {
        "real_regression_flags": real_flags,
        "random_regression_flags": rand_flags,
        "real_regression_rate": round(real_rate, 4),
        "random_regression_rate": round(rand_rate, 4),
        "real_errors": real_errors,
        "random_errors": rand_errors,
        "is_ssm_signal_non_null": is_signal_non_null,
        "verdict": (
            "SSM signal is non-null (random-mamba flags significantly fewer regressions)"
            if is_signal_non_null
            else "SSM signal may be null (random-mamba flags similar rate)"
        ),
    }


def main() -> int:
    pairs: list[dict[str, str]] = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"Skipping invalid JSON: {line[:80]}...\n")
            continue
        if isinstance(obj, dict) and "before" in obj and "after" in obj:
            pairs.append(obj)
        else:
            sys.stderr.write(f"Skipping object missing before/after: {line[:80]}...\n")

    if not pairs:
        sys.stderr.write(
            "No valid input pairs. Pipe JSONL with 'before' and 'after' fields.\n"
        )
        return 1

    results = run_control(pairs)
    summary = summarize(results)
    output = {**summary, "per_pair": results}
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
