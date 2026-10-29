"""Immutable, build-specific OMP containment evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from src import _install_paths
from src._init import InitResult
from src._omp_doctor import (
    PLUGIN_GATE, POLICY_FILES, REQUIRED_EVIDENCE, _json_process, _plugin_path,
    check_omp, qualification_binding,
)
from src._omp_install import _safe, install_omp

REPORT = ".reasoning-core/omp-qualification.json"


def write_report(project: Path, report: dict) -> Path:
    path = _safe(project.resolve(), REPORT)
    if report.get("schema_version") != 1 or not all(
        report.get("evidence", {}).get(name) is True for name in REQUIRED_EVIDENCE
    ):
        raise ValueError("incomplete qualification")
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def qualify(project: Path) -> dict:
    project = project.resolve()
    checks = {row["name"]: row["ok"] for row in check_omp(project)}
    if not all(checks.get(name) is True for name in (
        "omp_installed", "omp_loaded", "omp_plugin_installed", "omp_plugin_loaded", "omp_authenticated",
    )):
        raise ValueError("installation, loading and authenticated scoring must pass")
    executable = shutil.which("omp")
    if not executable:
        raise ValueError("OMP unavailable")
    with tempfile.TemporaryDirectory(prefix="rc-omp-qualified-") as temporary:
        root = Path(temporary).resolve()
        manifest = root / ".reasoning-core/install.manifest"
        manifest.parent.mkdir()
        manifest.touch()
        install_omp(root, manifest, {
            "RC_PYTHON": _install_paths.python_executable(), "RC_REPO": _install_paths.rc_repo_path(),
        }, InitResult(root), strict=True)
        for relative in POLICY_FILES:
            source = project / relative
            if source.is_file():
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(source.read_bytes())
        environment = dict(os.environ, RC_AUDIT_ROOT=str(root / "audit"))
        runtime = _json_process([executable, "--rc-native-probe"],
                                {"project_dir": str(root), "qualify": True}, 120, env=environment)
        if not runtime or runtime.get("loaded") is not True or runtime.get("model_invoked") is not False:
            raise ValueError("actual OMP qualification probe failed")
        evidence = runtime.get("evidence", {})
        rows = []
        for path in (root / "audit").glob("*/*.jsonl"):
            rows.extend(json.loads(line) for line in path.read_text().splitlines())
        parent = runtime.get("session_id")
        child = runtime.get("child_session_id")
        receipt = next((row for row in rows if row.get("session_id") == parent
                        and row.get("tool_call_id") == "rc-qualify-allow"
                        and row.get("reason") == "receipt_verified"), None)
        evidence["receipt_verified"] = receipt is not None
        evidence["child_guard_retained"] = evidence.get("child_guard_retained") is True and any(
            row.get("session_id") == child and row.get("tool_call_id") == "rc-qualify-child"
            and row.get("decision") == "blocked" for row in rows
        )
        probe = Path(__file__).with_name("omp_extension_probe.ts")
        bun = shutil.which("bun")
        if not bun:
            raise ValueError("bun unavailable for extension probe")
        probe_environment = dict(os.environ, RC_AUDIT_ROOT=str(root / "audit"), RC_MODE="copilot")
        probe_run = subprocess.run(
            [bun, str(probe), str(root), str(_plugin_path(project) / PLUGIN_GATE)], capture_output=True, text=True,
            env=probe_environment, cwd=root, timeout=120, check=False,
        )
        if probe_run.returncode != 0:
            raise ValueError("extension probe failed: " + probe_run.stderr.strip()[-300:])
        try:
            summary = json.loads(probe_run.stdout.strip().splitlines()[-1])
        except ValueError as exc:
            raise ValueError("extension probe produced no summary") from exc
        evidence["extension_bash_screened"] = (
            summary.get("bash_allowed") is True and summary.get("bash_blocked") is True
        )
        evidence["extension_write_roundtrip"] = summary.get("write_roundtrip") is True
        evidence["extension_subagent_recorded"] = summary.get("subagent_recorded") is True
        evidence["extension_advisor_recorded"] = summary.get("advisor_recorded") is True
        if not all(evidence.get(name) is True for name in REQUIRED_EVIDENCE):
            raise ValueError("qualification evidence incomplete: " + json.dumps(evidence, sort_keys=True))
        return {
            "schema_version": 1, "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "binding": qualification_binding(project, runtime), "evidence": evidence,
            "baseline_context": "baseline-2026-08-09",
            "model": {"provider": "rc-qualification", "id": "rc-no-provider", "invoked": False},
            "coverage": ["native_hashline_edit", "whole_batch_deny", "receipt", "sdk_child",
                         "extension_bash_screened", "extension_write_roundtrip",
                         "extension_subagent_recorded", "extension_advisor_recorded"],
            "exclusions": ["task_spawn", "isolated_merge", "shell", "eval", "plain_write",
                           "external_filesystem_races", "model_behavior"],
            "receipt_sha256": hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest(),
            "audit": rows,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        report = qualify(args.project_dir)
        print(write_report(args.project_dir, report))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
