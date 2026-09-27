"""Prepare four arm predictions and invoke the official FeatureBench grader."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ARMS = ("vanilla", "laya", "rc", "laya_rc")
DEFAULT_CASES = Path(__file__).with_name("cases.json")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--featurebench", type=Path, required=True, help="official source checkout")
    parser.add_argument("--python", type=Path, required=True, help="Python with FeatureBench dependencies")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    rollout, out, upstream = args.rollout.resolve(), args.out.resolve(), args.featurebench.resolve()
    if out.exists() or args.timeout < 1:
        parser.error("grading output must be new and timeout positive")
    manifest = json.loads((rollout / "run_manifest.json").read_text())
    cases_data = args.cases.read_bytes()
    if manifest["cases_sha256"] != sha256(cases_data):
        parser.error("rollout does not match pinned cases")
    expected = {(item["instance_id"], arm) for item in json.loads(cases_data)["cases"]
                for arm in ARMS}
    results = {(row["instance_id"], row["arm"]): row for row in manifest["results"]}
    if set(results) != expected:
        parser.error("rollout is incomplete or case set drifted")
    out.mkdir(parents=True)
    grade_manifest = {
        "schema_version": 1, "rollout_manifest_sha256": sha256((rollout / "run_manifest.json").read_bytes()),
        "dataset_revision": manifest["dataset_revision"], "benchmark": manifest["benchmark"],
        "split": manifest["split"], "official_harness_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=upstream, capture_output=True,
            text=True, check=True,
        ).stdout.strip(), "arms": {},
    }
    try:
        for arm in ARMS:
            arm_dir = out / arm
            arm_dir.mkdir()
            predictions = []
            for instance_id, current_arm in sorted(expected):
                if current_arm != arm:
                    continue
                row = results[(instance_id, arm)]
                patch_path = rollout / instance_id / arm / "final.patch"
                patch = patch_path.read_bytes()
                if sha256(patch) != row["patch_sha256"]:
                    raise ValueError(f"patch hash mismatch: {instance_id} {arm}")
                predictions.append({
                    "instance_id": instance_id, "n_attempt": 1,
                    "model_patch": patch.decode("utf-8"),
                    "agent": f"rc-four-arm-{arm}", "model": manifest["model"],
                    "task_metadata": {}, "success": row["agent_exit_code"] == 0 and bool(patch),
                    "error": None if row["agent_exit_code"] == 0 else "rollout_error",
                    "agent_exit_status": str(row["agent_exit_code"]),
                })
            predictions_path = arm_dir / "output.jsonl"
            predictions_path.write_text(
                "".join(json.dumps(item) + "\n" for item in predictions), encoding="utf-8"
            )
            command = [
                str(args.python.absolute()), "-m", "featurebench.harness.run_evaluation",
                "--predictions-path", str(predictions_path), "--dataset", manifest["benchmark"],
                "--data-version", manifest["dataset_revision"], "--split", manifest["split"],
                "--n-concurrent", "1", "--timeout", str(args.timeout), "--include-failed",
            ]
            with (arm_dir / "grader.log").open("wb") as log:
                status = subprocess.run(
                    command, cwd=upstream, env={**os.environ, "PYTHONPATH": str(upstream)},
                    stdout=log, stderr=subprocess.STDOUT, check=False,
                ).returncode
            grade_manifest["arms"][arm] = {
                "exit_code": status, "predictions_sha256": sha256(predictions_path.read_bytes()),
                "report_exists": (arm_dir / "report.json").is_file(),
            }
            print(f"{arm}: grader exit {status}", flush=True)
    finally:
        (out / "grade_manifest.json").write_text(json.dumps(grade_manifest, indent=2) + "\n")
    return 0 if all(item["exit_code"] == 0 for item in grade_manifest["arms"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
