#!/usr/bin/env python3
"""Suite runner for the reasoning-core eval.

Reads the dataset JSON, samples N tasks deterministically, runs both arms
for each task via ``eval/run_task.sh``, streams per-task results to
``<out_dir>/results.jsonl`` and finally invokes ``eval/aggregate.py`` to
produce ``report.{md,json}``.

Usage:
    python3 eval/run_suite.py --n 5
    python3 eval/run_suite.py --task-ids django__django-11099,sympy__sympy-13971 --arms vanilla,treatment

In dry-run mode (default unless ``RC_LIVE=1``) the runner only prints the
schedule and exits 0. CI uses dry-run as a smoke test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "eval" / "datasets" / "swe_bench_verified_python_subset.json"
DEFAULT_OUT_BASE = REPO_ROOT / "eval" / "runs"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else None
    except OSError:
        return None


def _write_run_manifest(out_dir: Path, *, run_id: str, dataset: Path, tasks: list[dict[str, Any],], arms: list[str], seed: int, schedule: list[tuple[str, str]], live: bool) -> Path:
    """Freeze inputs before execution so reports remain comparable later."""
    arm_config = {arm: {key: value for key, value in os.environ.items() if key.startswith("RC_") or key.startswith("S2_")} for arm in arms}
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dataset": {"path": str(dataset), "sha256": _sha256(dataset)},
        "task_ids": [task["instance_id"] for task in tasks],
        "seed": seed,
        "arms": arms,
        "schedule": [{"task_id": task_id, "arm": arm} for task_id, arm in schedule],
        "arm_configuration": arm_config,
        "configuration_hash": hashlib.sha256(json.dumps(arm_config, sort_keys=True).encode()).hexdigest(),
        "code_sha": _git_head(),
        "live": live,
        "raw_artifact_path": str(out_dir / "results.jsonl"),
    }
    path = out_dir / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Orchestrate paired vanilla/treatment runs for the reasoning-core eval.",
    )
    p.add_argument("--n", type=int, default=5, help="number of tasks to sample (default 5, smoke)")
    p.add_argument("--arms", default="vanilla,treatment", help="comma-separated arms (default vanilla,treatment)")
    p.add_argument("--task-ids", default=None, help="explicit comma-separated task IDs (overrides --n)")
    p.add_argument("--dataset", default=str(DEFAULT_DATASET))
    p.add_argument("--out-dir", default=None, help="run output dir (default eval/runs/<run_id>)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--task-timeout", type=int, default=900, help="per-arm wall-clock cap in seconds")
    p.add_argument("--skip-aggregate", action="store_true", help="do not invoke aggregate.py at the end")
    p.add_argument("--run-id", default=None,
                   help="explicit run-id label (default = utc timestamp). "
                        "CI passes the GitHub run number for traceability.")
    return p.parse_args(argv)


def _load_dataset(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _sample_tasks(ds: dict[str, Any], n: int, seed: int) -> list[dict[str, Any]]:
    tasks = list(ds.get("tasks", []))
    rng = random.Random(seed)
    rng.shuffle(tasks)
    return tasks[:n]


def _select_explicit(ds: dict[str, Any], task_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {t["instance_id"]: t for t in ds.get("tasks", [])}
    out = []
    for tid in task_ids:
        if tid not in by_id:
            raise SystemExit(f"unknown task_id: {tid}")
        out.append(by_id[tid])
    return out


def _build_schedule(tasks: list[dict[str, Any]], arms: list[str], seed: int) -> list[tuple[str, str]]:
    """Return [(task_id, arm), ...] with per-pair arm order randomised."""
    rng = random.Random(seed + 1)
    schedule: list[tuple[str, str]] = []
    for t in tasks:
        ordered = list(arms)
        rng.shuffle(ordered)
        for arm in ordered:
            schedule.append((t["instance_id"], arm))
    return schedule


def _run_one(task_id: str, arm: str, out_dir: Path, timeout_s: int) -> dict[str, Any]:
    script = REPO_ROOT / "eval" / "run_task.sh"
    cmd = ["bash", str(script), task_id, arm, str(out_dir)]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout_s + 60,  # outer cap; the script has its own kill
            capture_output=True,
            text=True,
        )
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        return {
            "task_id": task_id,
            "arm": arm,
            "ok": False,
            "timed_out": True,
            "duration_s": time.time() - started,
        }
    return {
        "task_id": task_id,
        "arm": arm,
        "ok": ok,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-400:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-400:] if proc.stderr else "",
        "duration_s": time.time() - started,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    supported_arms = {"vanilla", "advisory_shadow", "deterministic_only", "plan_grounding_only", "full_copilot", "treatment"}
    for arm in arms:
        if arm not in supported_arms:
            print(f"ERROR: unknown arm '{arm}'", file=sys.stderr)
            return 2

    ds = _load_dataset(Path(args.dataset))

    if args.task_ids:
        explicit = [t.strip() for t in args.task_ids.split(",") if t.strip()]
        tasks = _select_explicit(ds, explicit)
    else:
        tasks = _sample_tasks(ds, args.n, args.seed)

    if not tasks:
        print("ERROR: no tasks selected", file=sys.stderr)
        return 3

    schedule = _build_schedule(tasks, arms, args.seed)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir) if args.out_dir else (DEFAULT_OUT_BASE / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat()
    manifest_path = _write_run_manifest(out_dir, run_id=run_id, dataset=Path(args.dataset), tasks=tasks, arms=arms, seed=args.seed, schedule=schedule, live=os.environ.get("RC_LIVE", "0") == "1")
    print(f"[run_suite] run_id={run_id} tasks={len(tasks)} arms={arms} out={out_dir}")
    print(f"[run_suite] schedule:")
    for tid, arm in schedule:
        print(f"  - {tid} :: {arm}")

    if os.environ.get("RC_LIVE", "0") != "1":
        # Dry-run.
        summary = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": started_at,
            "n_tasks": len(tasks),
            "arms": arms,
            "schedule": [{"task_id": t, "arm": a} for t, a in schedule],
            "live": False,
            "run_manifest": str(manifest_path),
        }
        with open(out_dir / "run_summary.json", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print("[run_suite] dry-run; set RC_LIVE=1 to execute.")
        return 0

    # Live run.
    results_jsonl = out_dir / "results.jsonl"
    completed = 0
    timed_out = 0
    errored = 0
    with open(results_jsonl, "w", encoding="utf-8") as rf:
        for tid, arm in schedule:
            print(f"[run_suite] running {tid} :: {arm}")
            res = _run_one(tid, arm, out_dir, args.task_timeout)
            rf.write(json.dumps(res) + "\n")
            rf.flush()
            if res.get("timed_out"):
                timed_out += 1
            elif not res.get("ok"):
                errored += 1
            else:
                completed += 1

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "n_tasks": len(tasks),
        "arms": arms,
        "completed": completed,
        "timed_out": timed_out,
        "errored": errored,
        "live": True,
        "run_manifest": str(manifest_path),
    }
    with open(out_dir / "run_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"[run_suite] done. completed={completed} timed_out={timed_out} errored={errored}")

    if not args.skip_aggregate:
        agg_script = REPO_ROOT / "eval" / "aggregate.py"
        rc = subprocess.call(["python3", str(agg_script), str(out_dir)])
        if rc != 0:
            print(f"[run_suite] aggregate.py exited non-zero ({rc})", file=sys.stderr)

    # Bind the aggregate output after it exists without changing frozen inputs.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = out_dir / "report.json"
    if report.is_file():
        manifest["aggregate_report"] = {"path": str(report), "sha256": _sha256(report)}
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
