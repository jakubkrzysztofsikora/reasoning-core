"""Exploratory paired intake evaluation of local rules and a local Laya server."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.autonomous import LayaAdapter, LocalAdapter, TaskSpec

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("cases.json")
LABELS = {"localized", "multifile", "investigation"}


def load_cases(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("invalid triage fixture schema")
    cases = document["cases"]
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("case IDs must be nonempty and unique")
    for case in cases:
        if case["expected_kind"] not in LABELS or not case["id"] or not case["brief"]:
            raise ValueError("invalid case label, ID, or brief")
        TaskSpec.from_dict({
            "prompt": case["brief"], "triage_brief": case["brief"],
            "allowed_paths": case["allowed_paths"], "checks": [["true"]],
        })
    return cases


def summarize(rows: list[dict], source: str) -> dict:
    arm = [row for row in rows if row["source"] == source]
    durations = sorted(row["elapsed_ms"] for row in arm)
    answered = [row for row in arm if row["kind"] != "uncertain"]
    return {
        "n": len(arm),
        "correct": sum(row["kind"] == row["expected_kind"] for row in arm),
        "abstentions": len(arm) - len(answered),
        "median_ms": round(statistics.median(durations), 1),
        "p90_ms": round(durations[math.ceil(len(durations) * 0.9) - 1], 1),
    }


def server_health(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=3) as response:
            health = json.load(response)
        return {key: health.get(key) for key in ("status", "loaded", "device")}
    except (OSError, ValueError):
        return {"status": "unavailable"}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--laya-url", default="http://127.0.0.1:8000")
    parser.add_argument("--laya-model", choices=["english", "multilingual", "typed-decisions"],
                        default="english")
    parser.add_argument("--baseline-id", default="baseline-2026-09-27-laya-prepilot")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    adapters = {
        "rules": LocalAdapter(),
        "laya": LayaAdapter(args.laya_url, model=args.laya_model),
    }
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "purpose": "Exploratory task-intake labels only; no coding outcome or causal claim.",
        "baseline_id": args.baseline_id,
        "fixture_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256((ROOT / "src/autonomous.py").read_bytes()).hexdigest(),
        "code_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip(),
        "laya_model": args.laya_model,
        "laya_url": args.laya_url,
        "laya_health": server_health(args.laya_url),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_ids": [case["id"] for case in cases],
    }
    (out / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    rows = []
    for case in cases:
        task = TaskSpec.from_dict({
            "prompt": case["brief"], "triage_brief": case["brief"],
            "allowed_paths": case["allowed_paths"], "checks": [["true"]],
        })
        for source, adapter in adapters.items():
            started = time.monotonic()
            decision = adapter.decide(task)
            rows.append({
                "case_id": case["id"], "expected_kind": case["expected_kind"],
                "source": source, "kind": decision.kind, "status": decision.status,
                "confidence": decision.confidence, "model": decision.model,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "input_tokens": decision.input_tokens,
            })
    (out / "results.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    summary = {
        "rules": summarize(rows, "rules"),
        "laya": summarize(rows, "laya"),
        "note": "Synthetic, small, hand-labeled intake fixtures; results do not measure coding outcomes or calibration.",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
