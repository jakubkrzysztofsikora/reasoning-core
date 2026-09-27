"""Replay historical source patches against identical post-fix regression tests.

This is a commit-coupled diagnostic, not an independent agent comparison.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CASES = HERE / "cases.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str, cwd: Path = REPO, input_data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, input=input_data, capture_output=True,
        check=True, timeout=60,
    ).stdout


def command(argv: list[str], cwd: Path, log_path: Path) -> dict:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, timeout=180, check=False,
            env={**os.environ, "PYTHONPATH": str(cwd), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        code = result.returncode
        output = result.stdout + b"\n--- stderr ---\n" + result.stderr
        status = "passed" if code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        code, status = None, "timeout"
        output = (exc.stdout or b"") + b"\n--- stderr ---\n" + (exc.stderr or b"")
    log_path.write_bytes(output)
    return {
        "argv": argv, "status": status, "exit_code": code,
        "duration_s": round(time.perf_counter() - started, 3),
        "log": log_path.name, "log_sha256": sha256(output),
    }


def _validate_case(case: dict) -> tuple[str, str, bytes]:
    commit = case["commit"]
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("case commit must be a full SHA")
    parent = git("rev-parse", commit + "^").decode().strip()
    changed = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).decode().splitlines())
    expected = set(case["source_files"]) | {case["test_file"]}
    if not expected <= changed:
        raise ValueError(f"{case['id']}: manifest paths not all changed in commit")
    if any(not name.startswith("test_") for name in case["focused_tests"]):
        raise ValueError(f"{case['id']}: invalid focused test")
    patch = git("diff", "--binary", parent, commit, "--", *case["source_files"])
    if not patch:
        raise ValueError(f"{case['id']}: empty source patch")
    return parent, commit, patch


def replay(case: dict, run_dir: Path) -> dict:
    parent, commit, patch = _validate_case(case)
    case_dir = run_dir / case["id"]
    case_dir.mkdir()
    test_file = case["test_file"]
    test_bytes = git("show", f"{commit}:{test_file}")
    result = {
        "id": case["id"], "parent": parent, "commit": commit,
        "source_files": case["source_files"], "test_file": test_file,
        "focused_tests": case["focused_tests"],
        "source_patch_sha256": sha256(patch), "test_sha256": sha256(test_bytes),
        "arms": {},
    }
    for arm in ("base", "patched"):
        checkout = case_dir / arm
        git("clone", "--quiet", "--no-hardlinks", str(REPO), str(checkout))
        git("checkout", "--quiet", "--detach", parent, cwd=checkout)
        target = checkout / test_file
        target.write_bytes(test_bytes)
        if arm == "patched":
            git("apply", "--check", "-", cwd=checkout, input_data=patch)
            git("apply", "-", cwd=checkout, input_data=patch)
        if target.read_bytes() != test_bytes:
            raise RuntimeError("test overlay changed during patch")
        selectors = [f"{test_file}::{name}" for name in case["focused_tests"]]
        checks = {
            "focused": command(
                [sys.executable, "-m", "pytest", "-q", *selectors],
                checkout, case_dir / f"{arm}-focused.log",
            ),
            "test_file": command(
                [sys.executable, "-m", "pytest", "-q", test_file],
                checkout, case_dir / f"{arm}-test-file.log",
            ),
        }
        parse_errors = []
        for path in case["source_files"]:
            try:
                ast.parse((checkout / path).read_text(encoding="utf-8"), filename=path)
            except (SyntaxError, UnicodeError) as exc:
                parse_errors.append({"path": path, "error_type": type(exc).__name__})
        checks["python_parse"] = {"status": "passed" if not parse_errors else "failed", "errors": parse_errors}
        checks["ruff"] = command(
            [sys.executable, "-m", "ruff", "check", *case["source_files"]],
            checkout, case_dir / f"{arm}-ruff.log",
        )
        result["arms"][arm] = checks
    base = result["arms"]["base"]["focused"]["status"]
    patched = result["arms"]["patched"]["focused"]["status"]
    result["focused_replay"] = (
        "red_green" if base == "failed" and patched == "passed"
        else "inconclusive" if "timeout" in (base, patched)
        else "other"
    )
    result["historical_agent_runtime"] = "unknown"
    result["historical_usability"] = "unknown"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new run directory")
    args = parser.parse_args()
    cases_bytes = CASES.read_bytes()
    cases = json.loads(cases_bytes)["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or any("/" in name or name in (".", "..") for name in ids):
        parser.error("case IDs must be unique directory names")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": git("rev-parse", "HEAD").decode().strip(),
        "cases_sha256": sha256(cases_bytes),
        "baseline_id": "baseline-2026-09-27-real-session-replay-prepilot",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "replay_scope": "historical commits with same commit-added tests in both arms",
        "cases": [],
    }
    try:
        for case in cases:
            manifest["cases"].append(replay(case, output))
    finally:
        (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for case in manifest["cases"]:
        print(f"{case['id']}: {case['focused_replay']}")
    return 0 if all(case["focused_replay"] == "red_green" for case in manifest["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
