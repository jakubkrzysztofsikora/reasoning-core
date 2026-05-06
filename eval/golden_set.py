"""Golden-set regression gate (P4 tracker #32).

50 hand-curated benign edits — known to be safe, low-risk changes
representative of real engineering work. Used as the regression gate
before promoting any P1/P2/P3 invariant from shadow → enforce. Zero
blocks required.

Each entry: (file_path, before, after, expected_decision="allowed").
The set ships empty; operators populate by adding rows from their own
git history of merged-and-stable PRs that should NEVER block.

Usage:
    python -m eval.golden_set --check eval/calibrated/labels.jsonl
        # Loads labels.jsonl, replays each as a /score call, reports
        # blocks vs allowed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

GOLDEN_SET_PATH = Path("eval/golden_set.jsonl")


def load(path: Path = GOLDEN_SET_PATH) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def replay_against_sidecar(rows: Iterable[dict], sidecar_url: str = "http://127.0.0.1:8765") -> dict:
    """POST each row to sidecar /score; tally blocks vs allowed.

    Returns {"total": N, "blocked": K, "allowed": M, "errors": E}.
    Promotion gate requires blocked == 0.
    """
    import urllib.request
    counts = {"total": 0, "blocked": 0, "allowed": 0, "errors": 0}
    for row in rows:
        counts["total"] += 1
        body = json.dumps({
            "path": row.get("path", ""),
            "before_src": row.get("before", ""),
            "after_src": row.get("after", ""),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{sidecar_url}/score",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                report = json.loads(resp.read().decode("utf-8"))
            if report.get("regression_detected") is True:
                counts["blocked"] += 1
            else:
                counts["allowed"] += 1
        except Exception:  # noqa: BLE001
            counts["errors"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", type=str, default=str(GOLDEN_SET_PATH))
    ap.add_argument("--sidecar", default="http://127.0.0.1:8765")
    args = ap.parse_args()
    rows = load(Path(args.check))
    if not rows:
        sys.stderr.write(
            f"WARN: golden set at {args.check} is empty — populate before "
            f"using as a promotion gate.\n"
        )
        return 1
    counts = replay_against_sidecar(rows, args.sidecar)
    sys.stdout.write(json.dumps(counts, indent=2))
    return 0 if counts["blocked"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
