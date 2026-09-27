"""Exploratory four-arm FeatureBench rollout on an isolated host checkout.

Official FeatureBench grading must be run separately; no gold fields enter the agent.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from src.autonomous import LayaAdapter, TaskSpec, _qualification, _sidecar_healthy

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("cases.json")
HOOK = ROOT / "src" / "hooks" / "pre_edit_guard.py"
MODEL = "claude-sonnet-4-5"
ARMS = ("vanilla", "laya", "rc", "laya_rc")
# Sphinx fixture blobs have intentionally invalid Latin-1 working-tree content.
CHECKOUT_ARTIFACTS = (
    "tests/roots/test-root/wrongenc.inc",
    "tests/roots/test-warnings/wrongenc.inc",
)
PLAN = "# Benchmark task scope\n\nImplement the requested Python source fix. Preserve tests and harness policy.\n"
CONTRACT = """version: v1
allowed_paths:
  - '**/*.py'
forbidden_paths:
  - 'tests/**'
  - 'testing/**'
  - '**/test_*.py'
  - '.reasoning-core/**'
"""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        timeout=120,
    )
    return result.stdout.rstrip("\n")


def task_row(case: dict, revision: str) -> dict:
    query = urllib.parse.urlencode({
        "dataset": "LiberCoders/FeatureBench", "config": "default",
        "split": "fast", "offset": case["row_index"], "length": 1,
        "revision": revision,
    })
    with urllib.request.urlopen(
        "https://datasets-server.huggingface.co/rows?" + query, timeout=30
    ) as response:
        entry = json.load(response)["rows"][0]
    if entry["row_idx"] != case["row_index"]:
        raise ValueError("dataset row index drifted")
    row = entry["row"]
    for key in ("instance_id", "repo", "base_commit"):
        if row[key] != case[key]:
            raise ValueError(f"dataset {key} drifted")
    if digest(row["problem_statement"].encode()) != case["problem_sha256"]:
        raise ValueError("problem statement drifted")
    return {
        "problem_statement": row["problem_statement"],
        "mask_patch": row["patch"],
        "fail_to_pass_files": row["FAIL_TO_PASS"],
    }


def environment(workspace: Path, audit: Path, guarded: bool) -> dict[str, str]:
    clean = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("RC_", "S2_", "ANTHROPIC_", "CLAUDE_"))
        or key == "CLAUDE_CODE_OAUTH_TOKEN"
    }
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    if guarded:
        clean.update({
            "RC_HOST": "claude", "RC_PROJECT_DIR": str(workspace),
            "RC_AUDIT_ROOT": str(audit), "RC_MODE": "copilot",
            "RC_SHADOW_MODE": "0", "RC_PLAN_GROUNDING": "2",
            "RC_PLAN_BLOCK": "1", "RC_RULE_ENGINE": "1",
            "RC_NEURAL_CORROBORATED": "1", "S2_URL": "http://127.0.0.1:8765",
            "S2_FAIL_CLOSED": "1",
        })
    return clean


def run_arm(
    case: dict, row: dict, arm: str, source: Path, root: Path,
    *, budget: float, timeout: int,
) -> dict:
    arm_dir = root / case["instance_id"] / arm
    arm_dir.mkdir(parents=True)
    workspace = arm_dir / "workspace"
    workspace.mkdir()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", case["base_commit"]], cwd=source,
        check=True, capture_output=True, timeout=120,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(workspace, filter="data")
    mask = row["mask_patch"].encode()
    subprocess.run(
        ["git", "apply", "--check", "-"], cwd=workspace, input=mask,
        check=True, capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "apply", "--whitespace=fix", "-"], cwd=workspace, input=mask,
        check=True, capture_output=True, timeout=30,
    )
    for relative in row["fail_to_pass_files"]:
        candidate = PurePosixPath(relative.removeprefix("/testbed/"))
        if (candidate.is_absolute() or ".." in candidate.parts
                or not (workspace / str(candidate)).resolve().is_relative_to(workspace)):
            raise ValueError("unsafe hidden test path")
        (workspace / str(candidate)).unlink(missing_ok=True)
    policy = workspace / ".reasoning-core"
    policy.mkdir()
    (policy / "contract.yaml").write_text(CONTRACT, encoding="utf-8")
    (workspace / "PLAN.md").write_text(PLAN, encoding="utf-8")
    git(workspace, "init", "--quiet")
    git(workspace, "add", "-A", "--", ".",
        *(f":(exclude){path}" for path in CHECKOUT_ARTIFACTS))
    git(workspace, "-c", "user.name=FeatureBench",
        "-c", "user.email=featurebench@example.invalid",
        "commit", "--quiet", "-m", "masked task baseline")
    initial_dirty = git(workspace, "status", "--porcelain", "-uno").splitlines()
    if initial_dirty:
        raise RuntimeError("masked task baseline has tracked changes")
    guarded = arm in ("rc", "laya_rc")
    settings = {"model": MODEL}
    if guarded:
        settings["hooks"] = {"PreToolUse": [{
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": f"{sys.executable} {HOOK}",
                       "timeout": 60000}],
        }]}
    settings_path = arm_dir / "settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    if guarded:
        probe = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(workspace / "test_forbidden.py"),
                "content": "x = 1\n",
            },
            "cwd": str(workspace),
            "session_id": "featurebench-preflight",
        }
        checked = subprocess.run(
            [sys.executable, str(HOOK)], cwd=workspace,
            env=environment(workspace, arm_dir / "preflight_audit", True),
            input=json.dumps(probe).encode(), capture_output=True, timeout=30,
        )
        if checked.returncode != 2 or b"forbidden_path" not in checked.stderr:
            raise RuntimeError("reasoning-core path gate preflight did not deny test write")
    decision = None
    prompt = (
        row["problem_statement"].rstrip()
        + "\n\nThe repository checkout is the current directory. "
        "Work only in Python source files; do not modify tests. "
        "Use the available file tools. Finish with your implementation."
    )
    if arm in ("laya", "laya_rc"):
        task = TaskSpec.from_dict({
            "prompt": row["problem_statement"], "allowed_paths": ["repository.py"],
            "checks": [["true"]], "triage_brief": row["problem_statement"][:1800],
        })
        decision = LayaAdapter().decide(task)
        if decision.status != "ok":
            raise RuntimeError(f"Laya decision unavailable: {decision.status}")
        prompt += "\n\n" + decision.hint()
    command = [
        "claude", "--print", "--output-format", "json", "--model", MODEL,
        "--permission-mode", "bypassPermissions", "--permission-prompts", "none",
        "--strict-mcp-config", "--setting-sources", "", "--settings", str(settings_path),
        "--tools", "Read,Edit,Write,Glob,Grep", "--max-budget-usd", str(budget), prompt,
    ]
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command, cwd=workspace, env=environment(workspace, arm_dir / "audit", guarded),
            capture_output=True, timeout=timeout, check=False,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
    elapsed = round(time.perf_counter() - started, 2)
    (arm_dir / "agent.stdout.json").write_bytes(stdout)
    (arm_dir / "agent.stderr.log").write_bytes(stderr)
    try:
        agent = json.loads(stdout)
    except (ValueError, UnicodeError):
        agent = {}
    # The frozen policy file is the same in every arm and never enters a patch.
    git(workspace, "add", "-A", "--", ".",
        *(f":(exclude){path}" for path in CHECKOUT_ARTIFACTS))
    patch = subprocess.run(
        ["git", "diff", "--cached", "--binary", "HEAD"], cwd=workspace,
        check=True, capture_output=True, timeout=30,
    ).stdout
    (arm_dir / "final.patch").write_bytes(patch)
    changed = git(workspace, "diff", "--cached", "--name-only", "HEAD").splitlines()
    test_changes = [path for path in changed if path.startswith(("tests/", "testing/"))
                    or Path(path).name.startswith("test_")]
    result = {
        "arm": arm, "guarded": guarded, "laya": arm in ("laya", "laya_rc"),
        "agent_exit_code": exit_code, "agent_is_error": agent.get("is_error"),
        "cost_usd": agent.get("total_cost_usd"), "elapsed_s": elapsed,
        "decision": decision.__dict__ if decision else None,
        "changed_paths": changed, "test_changes": test_changes,
        "masked_baseline_sha": git(workspace, "rev-parse", "HEAD"),
        "mask_patch_sha256": digest(mask),
        "patch_sha256": digest(patch), "patch_bytes": len(patch),
        "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr),
        "benchmark_grade": "pending_official_evaluation",
    }
    (arm_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="local clone of task repo")
    parser.add_argument("--out", type=Path, required=True, help="new directory outside this repo")
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--max-budget-usd", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.out.resolve()
    if output.is_relative_to(ROOT) or output.exists() or args.max_budget_usd <= 0:
        parser.error("output must be new and outside the source repository; budget must be positive")
    if subprocess.run(["claude", "--version"], capture_output=True, text=True,
                      check=True).stdout.strip() != "2.1.282 (Claude Code)":
        parser.error("Claude host version has changed; requalify before running")
    qualification = _qualification(args.qualification, MODEL, "2.1.282 (Claude Code)")
    if not _sidecar_healthy("http://127.0.0.1:8765"):
        parser.error("reasoning-core sidecar is unhealthy")
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
        health = json.load(response)
    if health.get("status") != "ok" or "english" not in health.get("loaded", []):
        parser.error("Laya English checkpoint is not loaded")
    cases_data = CASES.read_bytes()
    frozen = json.loads(cases_data)
    rows = [(case, task_row(case, frozen["dataset_revision"])) for case in frozen["cases"]]
    if any(git(source, "rev-parse", case["base_commit"]) != case["base_commit"]
           for case, _ in rows):
        parser.error("source clone lacks a pinned base commit")
    output.mkdir(parents=True)
    order = [(case, row, arm) for case, row in rows for arm in ARMS]
    random.Random(args.seed).shuffle(order)
    manifest = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_sha256": digest(cases_data), "baseline_id": frozen["baseline_id"],
        "dataset_revision": frozen["dataset_revision"], "benchmark": frozen["benchmark"],
        "split": frozen["split"], "qualification": qualification,
        "model": MODEL, "host_version": "2.1.282 (Claude Code)",
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "seed": args.seed, "max_budget_usd_per_arm": args.max_budget_usd,
        "timeout_s_per_arm": args.timeout, "order": [
            [case["instance_id"], arm] for case, _, arm in order
        ], "results": [],
        "scope": "exploratory host rollout; official FeatureBench grading pending",
    }
    try:
        for case, row, arm in order:
            print(f"running {case['instance_id']} {arm}", flush=True)
            result = run_arm(
                case, row, arm, source, output,
                budget=args.max_budget_usd, timeout=args.timeout,
            )
            manifest["results"].append({"instance_id": case["instance_id"], **result})
            print(f"finished {arm}: exit={result['agent_exit_code']} "
                  f"patch_bytes={result['patch_bytes']}", flush=True)
    finally:
        (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
