#!/usr/bin/env python3
"""Phase 6: per-host sidecar-decision agreement on `sidecar_block_pairs.jsonl`.

Hits the local sidecar /score endpoint directly (no LLM in the loop) and
compares the host-attributed decision against the dataset's
`expected_decision`. Run once per host with the matching `RC_HOST=<host>`
env so the audit log slices cleanly per host.

Usage::

    RC_HOST=claude  python -m eval.sidecar_agreement
    RC_HOST=gemini  python -m eval.sidecar_agreement
    RC_HOST=copilot python -m eval.sidecar_agreement
    RC_HOST=vibe    python -m eval.sidecar_agreement

Reports per-host agreement % + per-fixture decision breakdown so
disagreements are diagnosable, not just countable. Threshold for the
multi-cli matrix is derived from a per-host single-number baseline (NOT
per-(host,fixture) — would be 4×30×30 = 3,600 sessions per refresh).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "eval" / "datasets" / "sidecar_block_pairs.jsonl"
SIDECAR = os.environ.get("S2_URL", "http://127.0.0.1:8765") + "/score"


def main(argv: list[str]) -> int:
    host = os.environ.get("RC_HOST", "unknown")
    pairs = [json.loads(l) for l in DATASET.read_text().splitlines() if l.strip()]
    rows = []
    agree = 0
    for p in pairs:
        path_ext = {"python": ".py", "typescript": ".ts", "csharp": ".cs", "sql": ".sql"}.get(
            p.get("language", "python"), ".py"
        )
        path = f"/tmp/sidecar_block_pairs/{p['id']}{path_ext}"
        body = {
            "path": path,
            "before_src": p["before"],
            "after_src": p["after"],
            "host": host,
            "session_id": f"agreement-{host}-{int(time.time())}",
        }
        try:
            r = httpx.post(SIDECAR, json=body, timeout=30.0)
            report = r.json() if r.status_code == 200 else {}
            actual = "block" if report.get("regression_detected") else "allow"
        except Exception as exc:  # noqa: BLE001
            report = {"error": repr(exc)}
            actual = "error"
        match = (actual == p["expected_decision"])
        agree += int(match)
        rows.append({
            "id": p["id"],
            "expected": p["expected_decision"],
            "actual": actual,
            "match": match,
            "report_summary": report.get("human_summary", "")[:120],
        })
    pct = agree / len(pairs) if pairs else 0.0
    out = {
        "host": host,
        "n": len(pairs),
        "agreement": round(pct, 4),
        "rows": rows,
    }
    print(json.dumps(out, indent=2))
    return 0 if agree == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
