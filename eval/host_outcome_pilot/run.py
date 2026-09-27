#!/usr/bin/env python3
"""Run a preregistered Claude Code feedback-timing outcome pilot.

The treatment uses the shipped PreToolUse gate. The control permits edits then
returns equivalent deterministic policy feedback from a PostToolUse hook.
This study measures one pinned Claude Code host configuration only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "src" / "hooks" / "pre_edit_guard.py"
POST_WRITE = ROOT / "eval" / "host_outcome_pilot" / "post_write_feedback.py"
TASKS = ROOT / "eval" / "host_outcome_pilot" / "tasks.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must contain a non-empty tasks list")
    return tasks


def _health(url: str) -> dict[str, Any] | None:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _git_output(project: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, check=False)
    return result.stdout


def _write_fixture(project: Path, task: dict[str, Any]) -> None:
    (project / ".reasoning-core").mkdir(parents=True)
    (project / ".reasoning-core" / "rules.yaml").write_text(
        "corpus_version: v1\nrules:\n"
        "  - id: no_os_import\n    type: forbid_import\n    severity: deny\n    language: python\n    target: os\n"
        "    message: os import is forbidden\n",
        encoding="utf-8",
    )
    (project / ".reasoning-core" / "contract.yaml").write_text(
        "version: v1\nallowed_paths:\n  - src/service.py\n  - src/worker.py\n  - src/in_plan.py\n"
        "forbidden_paths:\n  - src/protected.py\nimport_rules:\n"
        "  - id: no_internal_import\n    severity: deny\n    scope: '**'\n"
        "    forbidden_imports:\n      - src.internal\n    message: internal module is protected\n",
        encoding="utf-8",
    )
    (project / "PLAN.md").write_text("# Plan\n\n- `src/in_plan.py`\n", encoding="utf-8")
    target = project / task["seed_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(task["seed"], encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "-c", "user.email=pilot@example.invalid", "-c", "user.name=Pilot", "commit", "--quiet", "-m", "seed"], cwd=project, check=True)


def _settings(arm: str, model: str) -> dict[str, Any]:
    command = f"{sys.executable} {HOOK}"
    hooks: dict[str, Any] = {"PreToolUse": [], "PostToolUse": []}
    if arm == "pre_write":
        hooks["PreToolUse"] = [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": command, "timeout": 60000}]}]
    elif arm == "post_write":
        hooks["PostToolUse"] = [{"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f"{sys.executable} {POST_WRITE}", "timeout": 15000}]}]
    else:
        raise ValueError(f"unknown arm: {arm}")
    return {"hooks": hooks, "model": model, "effortLevel": "medium"}


def _claude_gateway_env() -> dict[str, str]:
    """Preserve user-configured LiteLLM access when trial settings are isolated."""
    settings_path = Path.home() / ".claude" / "settings.local.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    configured = settings.get("env")
    if not isinstance(configured, dict):
        return {}
    return {
        key: value
        for key, value in configured.items()
        if key.startswith("ANTHROPIC_") and isinstance(value, str)
    }


def _environment(project: Path, audit_root: Path, sidecar_url: str, arm: str) -> dict[str, str]:
    remove = {key for key in os.environ if key.startswith("RC_") or key.startswith("S2_")}
    env = {key: value for key, value in os.environ.items() if key not in remove}
    # --setting-sources isolates trial hooks, so restore gateway credentials from
    # the user's local Claude settings without recording them in study artifacts.
    env.update(_claude_gateway_env())
    env.update({
        "RC_HOST": "claude", "RC_PROJECT_DIR": str(project), "RC_AUDIT_ROOT": str(audit_root),
        "RC_MODE": "copilot", "RC_SHADOW_MODE": "0", "RC_PLAN_GROUNDING": "2",
        "RC_PLAN_BLOCK": "1", "RC_RULE_ENGINE": "1", "RC_NEURAL_CORROBORATED": "1",
        "S2_URL": sidecar_url, "S2_HARD_CAP_MS": "10000", "S2_TIMEOUT": "30", "S2_FAIL_CLOSED": "1",
        "RC_EVAL_ARM": arm,
    })
    return env


def _events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in path.rglob("*.jsonl"):
        for line in item.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
    return events


def _run_trial(task: dict[str, Any], arm: str, root: Path, sidecar_url: str, budget: float, model: str) -> dict[str, Any]:
    project = root / "project"
    project.mkdir()
    _write_fixture(project, task)
    audit_root = root / "audit"
    settings_path = root / "settings.json"
    settings_path.write_text(json.dumps(_settings(arm, model)), encoding="utf-8")
    stream_path = root / "host-events.jsonl"
    command = [
        "claude", "--print", "--verbose", "--output-format", "stream-json", "--include-hook-events",
        "--model", model,
        "--permission-mode", "bypassPermissions", "--permission-prompts", "none",
        "--strict-mcp-config", "--setting-sources", "", "--settings", str(settings_path),
        "--disallowed-tools", "Bash,WebFetch,WebSearch,Task,MultiEdit,NotebookEdit,EnterWorktree,ExitWorktree",
        "--max-budget-usd", str(budget), task["prompt"],
    ]
    started = time.monotonic_ns()
    try:
        proc = subprocess.run(command, cwd=project, env=_environment(project, audit_root, sidecar_url, arm), capture_output=True, text=True, timeout=300)
        timed_out = False
    except subprocess.TimeoutExpired as error:
        proc = error
        timed_out = True
    elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
    stdout = proc.stdout if isinstance(proc, subprocess.CompletedProcess) else (proc.stdout or "")
    stderr = proc.stderr if isinstance(proc, subprocess.CompletedProcess) else (proc.stderr or "")
    stream_path.write_text(stdout, encoding="utf-8")
    host_events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            host_events.append(event)
    events = _events(audit_root)
    decisions = [event for event in events if event.get("decision") in {"allowed", "blocked", "shadow_blocked", "warn"}]
    blocked = [event for event in decisions if event.get("decision") in {"blocked", "shadow_blocked"}]
    final_diff = _git_output(project, "diff", "--binary")
    changed = _git_output(project, "diff", "--name-only").splitlines()
    final_policy_violation = any(event.get("decision") in {"blocked", "shadow_blocked"} for event in blocked)
    # The final source is rechecked by the shared oracle. A detected violation
    # here is a final outcome; an earlier treatment block alone is not one.
    check = subprocess.run([sys.executable, "-m", "eval.timing_pilot.check_policy", "--project", ".", "--path", task["seed_path"]], cwd=project, capture_output=True, text=True)
    final_policy_violation = check.returncode == 1
    result = {
        "task_id": task["id"], "category": task["category"], "arm": arm,
        "host_elapsed_ms": elapsed_ms, "host_returncode": None if timed_out else proc.returncode,
        "timed_out": timed_out, "host_stderr_tail": stderr[-1000:],
        "host_events": host_events,
        "host_failure_reason": next((event.get("rate_limit_info", {}).get("overageDisabledReason") for event in host_events if event.get("type") == "rate_limit_event"), None),
        "invalid_write_reached_worktree": arm == "post_write" and bool(blocked or "policy feedback" in stdout),
        "pre_write_blocks": len(blocked) if arm == "pre_write" else 0,
        "post_write_feedback_observed": arm == "post_write" and "policy feedback" in stdout,
        "final_policy_violation": final_policy_violation,
        "task_completion": (not timed_out and proc.returncode == 0 and not final_policy_violation),
        "execution_valid": (not timed_out and proc.returncode == 0),
        "changed_paths": changed, "final_diff_sha256": hashlib.sha256(final_diff.encode()).hexdigest(),
        "audit_event_count": len(events), "audit_events": events,
    }
    return result


def _report(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    arms = {arm: [row for row in rows if row["arm"] == arm] for arm in ("pre_write", "post_write")}
    summary: dict[str, Any] = {}
    for arm, arm_rows in arms.items():
        total = len(arm_rows)
        summary[arm] = {
            "runs": total,
            "final_policy_violations": sum(row["final_policy_violation"] for row in arm_rows),
            "completed": sum(row["task_completion"] for row in arm_rows),
            "invalid_writes_reached_worktree": sum(row["invalid_write_reached_worktree"] for row in arm_rows),
            "median_host_elapsed_ms": sorted(row["host_elapsed_ms"] for row in arm_rows)[total // 2] if total else None,
            "timeouts": sum(row["timed_out"] for row in arm_rows),
            "invalid_executions": sum(not row["execution_valid"] for row in arm_rows),
        }
    return {
        "schema_version": 1, "kind": "single_host_feedback_timing_outcome_pilot",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "configuration": config, "results": rows, "summary": summary,
        "limitations": [
            "Single Claude Code version, model, repository fixture, and operator; no generalization claim is supported.",
            "The synthetic tasks test explicit deterministic policies, not broad code quality or regression reduction.",
            "Outcome labels are deterministic final-policy checks, not blinded human labels.",
            "Do not compare the two arms as a model-quality result without randomized repeated runs and blinded labels.",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = ["# Host Outcome Pilot", "", "Preregistered single-host mechanism study; not a product-quality claim.", ""]
    lines += ["| Arm | Runs | Final policy violations | Completed | Invalid writes reaching worktree | Median host elapsed | Timeouts | Invalid executions |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in ("pre_write", "post_write"):
        row = report["summary"][arm]
        lines.append(f"| {arm} | {row['runs']} | {row['final_policy_violations']} | {row['completed']} | {row['invalid_writes_reached_worktree']} | {row['median_host_elapsed_ms']:.1f} ms | {row['timeouts']} | {row['invalid_executions']} |")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--max-budget-usd", type=float, default=1.0)
    parser.add_argument("--model", default="sonnet", help="Claude Code model identifier to pin for every trial")
    parser.add_argument("--sidecar-url", default=os.environ.get("S2_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    health = _health(args.sidecar_url)
    if not (health and health.get("model_loaded") is True):
        parser.error("Mamba sidecar must be ready with model_loaded=true")
    tasks = _load_tasks(args.tasks)
    out_dir = args.out_dir or ROOT / "eval" / "runs" / f"host-outcome-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out_dir.mkdir(parents=True, exist_ok=False)
    config = {"tasks_sha256": _sha256(args.tasks), "code_sha": _git_output(ROOT, "rev-parse", "HEAD").strip(), "host": "claude", "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False).stdout.strip(), "sidecar_health": health, "sidecar_url": args.sidecar_url, "model": args.model, "budget_usd_per_trial": args.max_budget_usd, "seed": args.seed}
    (out_dir / "manifest.json").write_text(json.dumps({"configuration": config, "tasks": tasks}, indent=2) + "\n", encoding="utf-8")
    rng = random.Random(args.seed)
    schedule = [(repeat, task, arm) for repeat in range(1, args.repeats + 1) for task in tasks for arm in ("pre_write", "post_write")]
    rng.shuffle(schedule)
    rows: list[dict[str, Any]] = []
    for sequence, (repeat, task, arm) in enumerate(schedule, 1):
        print(f"[host-outcome] {sequence}/{len(schedule)} {task['id']} {arm}", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="rc-host-outcome-") as temp:
            row = _run_trial(task, arm, Path(temp), args.sidecar_url, args.max_budget_usd, args.model)
        row.update({"repeat": repeat, "sequence": sequence})
        rows.append(row)
        with (out_dir / "raw.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    report = _report(rows, config)
    if any(not row["execution_valid"] for row in rows):
        report["valid_for_comparison"] = False
        report["comparison_failure_reason"] = "one or more host runs did not complete; inspect raw.jsonl"
    else:
        report["valid_for_comparison"] = True

    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"[host-outcome] wrote {out_dir / 'report.md'}")
    return 0 if report["valid_for_comparison"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
