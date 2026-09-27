"""Isolated same-model Codex/Laya/reasoning-core FeatureBench pilot."""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from eval.featurebench_four_arm.qualify_codex import BRIDGE, codex_command, hooks_config, sha256
from eval.featurebench_four_arm.run import ARMS, CONTRACT, PLAN, ROOT, git, task_row
from src.autonomous import LayaAdapter, TaskSpec, _sidecar_healthy

CASES = Path(__file__).with_name("cases_seaborn.json")


def stage(case: dict, row: dict, source: Path, workspace: Path) -> bytes:
    workspace.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", case["base_commit"]],
        cwd=source, check=True, capture_output=True, timeout=120,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(workspace, filter="data")
    mask = row["mask_patch"].encode()
    subprocess.run(["git", "apply", "--check", "-"], cwd=workspace,
                   input=mask, check=True, capture_output=True, timeout=30)
    subprocess.run(["git", "apply", "--whitespace=fix", "-"], cwd=workspace,
                   input=mask, check=True, capture_output=True, timeout=30)
    for relative in row["fail_to_pass_files"]:
        path = PurePosixPath(relative.removeprefix("/testbed/"))
        if path.is_absolute() or ".." in path.parts or not str(path).startswith("tests/"):
            raise ValueError("unsafe hidden test path")
        (workspace / str(path)).unlink(missing_ok=True)
    policy = workspace / ".reasoning-core"
    policy.mkdir()
    (policy / "contract.yaml").write_text(CONTRACT, encoding="utf-8")
    (workspace / "PLAN.md").write_text(PLAN, encoding="utf-8")
    git(workspace, "init", "--quiet")
    git(workspace, "add", "-A")
    git(workspace, "-c", "user.name=FeatureBench", "-c",
        "user.email=featurebench@example.invalid", "commit", "--quiet",
        "-m", "masked task baseline")
    if git(workspace, "status", "--porcelain", "-uno"):
        raise RuntimeError("masked task baseline is dirty")
    return mask


def run_arm(case: dict, row: dict, arm: str, source: Path, out: Path,
            model: str, timeout: int) -> dict:
    arm_dir = out / case["instance_id"] / arm
    workspace = arm_dir / "workspace"
    if workspace.exists():
        if not (workspace / ".git").exists() or git(workspace, "status", "--porcelain", "-uno"):
            raise RuntimeError("cannot resume a dirty or uninitialized arm workspace")
        mask = row["mask_patch"].encode()
    else:
        mask = stage(case, row, source, workspace)
    guarded = arm in ("rc", "laya_rc")
    decision = None
    prompt = (row["problem_statement"].rstrip()
              + "\n\nThe repository checkout is the current directory. "
              "Work only in Python source files; do not modify tests. "
              "Use apply_patch for writes and shell only for inspection and tests. "
              "Finish with your implementation.")
    if arm in ("laya", "laya_rc"):
        source_paths = [line.split(" b/", 1)[1] for line in row["mask_patch"].splitlines()
                        if line.startswith("diff --git a/") and " b/" in line]
        task = TaskSpec.from_dict({"prompt": row["problem_statement"],
                                   "allowed_paths": source_paths, "checks": [["true"]],
                                   "triage_brief": row["problem_statement"][:1800]})
        for attempt in range(3):
            decision = LayaAdapter(model="english").decide(task)
            if decision.status == "ok":
                break
            if attempt < 2:
                time.sleep(2)
        if decision.status != "ok":
            raise RuntimeError(f"Laya unavailable: {decision.status}")
        prompt += "\n\n" + decision.hint()
    command = codex_command(model, workspace, prompt)
    if not guarded:
        marker = hooks_config()
        command = [item for item in command if item != marker]
        flag_index = command.index("-c", command.index('web_search="disabled"') + 1)
        command.pop(flag_index)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RC_", "S2_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if guarded:
        env.update({"RC_PROJECT_DIR": str(workspace), "RC_AUDIT_ROOT": str(arm_dir / "audit"),
                    "RC_MODE": "copilot", "RC_PLAN_GROUNDING": "2", "RC_PLAN_BLOCK": "1",
                    "RC_RULE_ENGINE": "1", "RC_NEURAL_CORROBORATED": "1",
                    "S2_URL": "http://127.0.0.1:8765", "S2_FAIL_CLOSED": "1"})
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                              timeout=timeout)
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code, stdout, stderr = 124, exc.stdout or b"", exc.stderr or b""
    elapsed = round(time.monotonic() - started, 2)
    (arm_dir / "agent.stdout.jsonl").write_bytes(stdout)
    (arm_dir / "agent.stderr.log").write_bytes(stderr)
    usage = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
    git(workspace, "add", "-A")
    patch = subprocess.run(["git", "diff", "--cached", "--binary", "HEAD"],
                           cwd=workspace, check=True, capture_output=True).stdout
    (arm_dir / "final.patch").write_bytes(patch)
    changed = git(workspace, "diff", "--cached", "--name-only", "HEAD").splitlines()
    result = {"arm": arm, "guarded": guarded, "laya": bool(decision),
              "agent_exit_code": exit_code, "elapsed_s": elapsed, "usage": usage,
              "decision": decision.__dict__ if decision else None,
              "changed_paths": changed,
              "test_changes": [path for path in changed if path.startswith(("tests/", "testing/"))
                               or Path(path).name.startswith("test_")],
              "masked_baseline_sha": git(workspace, "rev-parse", "HEAD"),
              "mask_patch_sha256": sha256(mask), "patch_sha256": sha256(patch),
              "patch_bytes": len(patch), "stdout_sha256": sha256(stdout),
              "stderr_sha256": sha256(stderr),
              "benchmark_grade": "pending_host_proxy"}
    (arm_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--resume", action="store_true", help="resume an incomplete run without repeating completed arms")
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    if out.is_relative_to(ROOT) or args.timeout < 1 or (out.exists() != args.resume):
        parser.error("output must be new, or existing with --resume, outside source")
    qualification = json.loads(args.qualification.read_text())
    host_version = subprocess.run(["codex", "--version"], capture_output=True,
                                  text=True, check=True).stdout.strip()
    if (not qualification.get("qualified") or qualification.get("model") != args.model
            or qualification.get("codex_version") != host_version
            or qualification.get("bridge_sha256") != sha256(BRIDGE.read_bytes())):
        parser.error("Codex bridge qualification is absent or stale")
    if not _sidecar_healthy("http://127.0.0.1:8765"):
        parser.error("reasoning-core sidecar is unhealthy")
    cases_data = args.cases.read_bytes()
    frozen = json.loads(cases_data)
    if frozen["baseline_id"] != "baseline-2026-09-27-featurebench-seaborn-codex-prepilot":
        parser.error("wrong frozen baseline")
    rows = [(case, task_row(case, frozen["dataset_revision"])) for case in frozen["cases"]]
    for case, _ in rows:
        if git(source, "rev-parse", case["base_commit"]) != case["base_commit"]:
            parser.error("source clone lacks pinned commit")
    if not args.resume:
        out.mkdir(parents=True)
    order = [(case, row, arm) for case, row in rows for arm in ARMS]
    random.Random(args.seed).shuffle(order)
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "cases_sha256": sha256(cases_data), "baseline_id": frozen["baseline_id"],
                "dataset_revision": frozen["dataset_revision"], "benchmark": frozen["benchmark"],
                "split": frozen["split"], "qualification_sha256": sha256(args.qualification.read_bytes()),
                "model": args.model, "host_version": host_version,
                "runner_sha256": sha256(Path(__file__).read_bytes()), "bridge_sha256": sha256(BRIDGE.read_bytes()),
                "seed": args.seed, "timeout_s_per_arm": args.timeout,
                "order": [[case["instance_id"], arm] for case, _, arm in order], "results": [],
                "scope": "exploratory host proxy; apply_patch gate only; shell writes audited after run"}
    if args.resume:
        previous = json.loads((out / "run_manifest.json").read_text())
        if (previous["cases_sha256"] != manifest["cases_sha256"]
                or previous["qualification_sha256"] != manifest["qualification_sha256"]
                or previous["model"] != manifest["model"]
                or previous["order"] != manifest["order"]
                or previous["timeout_s_per_arm"] != manifest["timeout_s_per_arm"]):
            raise RuntimeError("incompatible run manifest")
        manifest = previous
        manifest["resume_runner_sha256"] = sha256(Path(__file__).read_bytes())
    completed = {(item["instance_id"], item["arm"]) for item in manifest["results"]}
    try:
        for case, row, arm in order:
            if (case["instance_id"], arm) in completed:
                continue
            print("running", case["instance_id"], arm, flush=True)
            result = run_arm(case, row, arm, source, out, args.model, args.timeout)
            manifest["results"].append({"instance_id": case["instance_id"], **result})
            print("finished", arm, result["agent_exit_code"], result["patch_bytes"], flush=True)
    finally:
        (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
