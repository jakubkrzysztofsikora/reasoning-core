"""Run pinned FeatureBench task tests locally when container grading is unavailable.

This is a host approximation, not an official FeatureBench score.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

ARMS = ("vanilla", "laya", "rc", "laya_rc")
DEFAULT_CASES = Path(__file__).with_name("cases.json")
F2P_TESTS = {
    96: ["tests/test_extensions/test_ext_doctest.py"],
    99: ["tests/test_command_line.py"],
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(source: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=source, check=True, capture_output=True, timeout=120,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    rollout, out, source = args.rollout.resolve(), args.out.resolve(), args.source.resolve()
    if out.exists() or args.timeout < 1:
        parser.error("output must be new and timeout positive")
    cases_data = args.cases.read_bytes()
    frozen = json.loads(cases_data)
    manifest_bytes = (rollout / "run_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest["cases_sha256"] != sha256(cases_data)
            or manifest["dataset_revision"] != frozen["dataset_revision"]):
        parser.error("rollout does not match pinned cases")
    expected = {(case["instance_id"], arm) for case in frozen["cases"] for arm in ARMS}
    results = {(row["instance_id"], row["arm"]): row for row in manifest["results"]}
    if set(results) != expected:
        parser.error("rollout is incomplete")
    out.mkdir(parents=True)
    report = {
        "schema_version": 1, "grade": "host_proxy_not_official_featurebench",
        "rollout_manifest_sha256": sha256(manifest_bytes),
        "dataset_revision": frozen["dataset_revision"], "results": [],
    }
    try:
        for case in frozen["cases"]:
            tests = case.get("fail_to_pass_files", F2P_TESTS.get(case["row_index"]))
            if not tests:
                raise ValueError("case has no pinned FAIL_TO_PASS test files")
            for arm in ARMS:
                row = results[(case["instance_id"], arm)]
                patch = (rollout / case["instance_id"] / arm / "final.patch").read_bytes()
                if sha256(patch) != row["patch_sha256"]:
                    raise ValueError(f"patch hash mismatch: {case['instance_id']} {arm}")
                trial = out / case["instance_id"] / arm
                trial.mkdir(parents=True)
                workspace = trial / "workspace"
                workspace.mkdir()
                archive = git(rollout / case["instance_id"] / arm / "workspace",
                              "archive", "--format=tar", "HEAD")
                with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                    bundle.extractall(workspace, filter="data")
                apply_exit = 0
                if patch:
                    apply_exit = subprocess.run(
                        ["git", "apply", "-"], cwd=workspace, input=patch,
                        capture_output=True, timeout=30,
                    ).returncode
                if apply_exit:
                    outcome = "patch_apply_error"
                    test_exit = None
                else:
                    for relative in tests:
                        safe = PurePosixPath(relative.removeprefix("/testbed/"))
                        if (safe.is_absolute() or ".." in safe.parts
                                or not str(safe).startswith(("tests/", "testing/"))):
                            raise ValueError("unsafe test path")
                        target = workspace / str(safe)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(git(source, "show", f"{case['base_commit']}:{safe}"))
                    env = {**os.environ, "PYTHONPATH": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
                    with (trial / "pytest.log").open("wb") as log:
                        try:
                            test_exit = subprocess.run(
                                [str(args.python.absolute()), "-m", "pytest", "-q",
                                 f"--junitxml={trial / 'junit.xml'}", *tests],
                                cwd=workspace, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=args.timeout,
                            ).returncode
                            outcome = "pass" if test_exit == 0 else "fail_or_infrastructure"
                        except subprocess.TimeoutExpired:
                            test_exit, outcome = 124, "timeout"
                junit = trial / "junit.xml"
                counts = None
                if junit.is_file():
                    suite = ET.parse(junit).getroot().find("testsuite")
                    if suite is not None:
                        counts = {key: int(suite.get(key, "0")) for key in
                                  ("tests", "failures", "errors", "skipped")}
                report["results"].append({
                    "instance_id": case["instance_id"], "arm": arm,
                    "patch_sha256": sha256(patch), "patch_applied": apply_exit == 0,
                    "test_exit_code": test_exit, "outcome": outcome, "counts": counts,
                })
                print(case["instance_id"], arm, outcome, flush=True)
    finally:
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
