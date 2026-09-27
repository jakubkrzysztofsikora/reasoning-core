#!/usr/bin/env python3
"""Run a deterministic feedback-timing pilot.

This measures the shipped pre-write hook against a named post-write control
check. It is a mechanism study, not an agent-quality or regression study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "src" / "hooks" / "pre_edit_guard.py"
DEFAULT_PROBES = ROOT / "eval" / "timing_pilot" / "probes.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_probes(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    probes = raw.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError("probes must contain a non-empty 'probes' list")
    return probes


def _write_fixture(project: Path, probe: dict[str, Any]) -> Path:
    (project / ".reasoning-core").mkdir(parents=True)
    (project / ".reasoning-core" / "rules.yaml").write_text(
        "corpus_version: v1\n"
        "rules:\n"
        "  - id: no_os_import\n"
        "    type: forbid_import\n"
        "    severity: deny\n"
        "    language: python\n"
        "    target: os\n"
        "    message: os import is forbidden\n"
        "  - id: no_subprocess_import\n"
        "    type: forbid_import\n"
        "    severity: deny\n"
        "    language: python\n"
        "    target: subprocess\n"
        "    message: subprocess import is forbidden\n",
        encoding="utf-8",
    )
    (project / ".reasoning-core" / "contract.yaml").write_text(
        "version: v1\n"
        "allowed_paths:\n"
        "  - src/service.py\n"
        "  - src/worker.py\n"
        "  - src/syntax.py\n"
        "  - src/in_plan.py\n"
        "forbidden_paths:\n"
        "  - src/protected.py\n"
        "import_rules:\n"
        "  - id: no_internal_import\n"
        "    severity: deny\n"
        "    scope: '**'\n"
        "    forbidden_imports:\n"
        "      - src.internal\n"
        "    message: internal module is protected\n",
        encoding="utf-8",
    )
    (project / "PLAN.md").write_text("# Plan\n\n- `src/in_plan.py`\n", encoding="utf-8")
    target = project / probe["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(probe["before"], encoding="utf-8")
    return target


def _env(project: Path, audit_root: Path, sidecar_url: str) -> dict[str, str]:
    blocked = {
        "RC_MODE", "RC_SHADOW_MODE", "RC_PLAN_GROUNDING", "RC_PLAN_BLOCK",
        "RC_RULE_ENGINE", "RC_RULE_ENGINE_ALLOW_BASIC_YAML", "RC_ORACLE_BLOCK",
        "RC_ORACLE_T1", "RC_ORACLE_T2", "RC_PROJECT_DIR", "RC_AUDIT_ROOT",
        "RC_NEURAL_CORROBORATED", "S2_URL", "S2_HARD_CAP_MS", "S2_FAIL_CLOSED", "S2_TIMEOUT",
    }
    env = {k: v for k, v in os.environ.items() if k not in blocked}
    env.update({
        "RC_MODE": "copilot",
        "RC_SHADOW_MODE": "0",
        "RC_PLAN_GROUNDING": "2",
        "RC_PLAN_BLOCK": "1",
        "RC_RULE_ENGINE": "1",
        "RC_PROJECT_DIR": str(project),
        "RC_AUDIT_ROOT": str(audit_root),
        "RC_NEURAL_CORROBORATED": "1",
        "S2_URL": sidecar_url,
        "S2_HARD_CAP_MS": "10000",
        "S2_TIMEOUT": "30",
        # Fail-open is required only for the explicit symbolic smoke mode.
        "S2_FAIL_CLOSED": "0",
    })
    return env


def _run_treatment(
    project: Path,
    target: Path,
    probe: dict[str, Any],
    audit_root: Path,
    sidecar_url: str,
    *,
    require_ssm: bool,
) -> dict[str, Any]:
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(target),
            "old_string": probe["before"],
            "new_string": probe["after"],
        },
    }
    started = time.monotonic_ns()
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
        capture_output=True, cwd=project, env=_env(project, audit_root, sidecar_url), timeout=30,
    )
    elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
    stderr = proc.stderr.strip()
    # A policy block can happen before scoring; require an explicit scored audit
    # receipt for a report that attributes timing to the default SSM path.
    scored = "sidecar unavailable" not in stderr and "symbolic fallback" not in stderr
    if require_ssm and not scored:
        outcome = "unscored"
    else:
        outcome = "blocked" if proc.returncode == 2 else "allowed"
    return {
        "latency_ms": elapsed_ms,
        "returncode": proc.returncode,
        "stderr": stderr,
        "stdout": proc.stdout.strip(),
        "scored": scored,
        "outcome": outcome,
    }


def _run_control(project: Path, target: Path, probe: dict[str, Any]) -> dict[str, Any]:
    write_started = time.monotonic_ns()
    target.write_text(probe["after"], encoding="utf-8")
    write_completed = time.monotonic_ns()
    started = time.monotonic_ns()
    proc = subprocess.run(
        probe["control"], cwd=project, text=True, capture_output=True, timeout=30,
    )
    finished = time.monotonic_ns()
    elapsed_ms = (finished - started) / 1_000_000
    return {
        "latency_ms": elapsed_ms,
        "end_to_end_latency_ms": (finished - write_started) / 1_000_000,
        "write_duration_ms": (write_completed - write_started) / 1_000_000,
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip(),
        "stdout": proc.stdout.strip(),
        "outcome": "failed" if proc.returncode else "passed",
    }


def _sidecar_health(sidecar_url: str) -> dict[str, Any] | None:
    try:
        with urlopen(f"{sidecar_url.rstrip('/')}/health", timeout=3) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def _report(results: list[dict[str, Any]], *, repeats: int, config: dict[str, Any], symbolic_fallback: bool) -> dict[str, Any]:
    probe_rows = []
    for probe_id in sorted({row["probe_id"] for row in results}):
        rows = [row for row in results if row["probe_id"] == probe_id]
        proto = rows[0]
        treatment = _median([row["treatment"]["latency_ms"] for row in rows])
        control = _median([row["control"]["latency_ms"] for row in rows])
        control_end_to_end = _median([row["control"]["end_to_end_latency_ms"] for row in rows])
        ratio = control / treatment if treatment > 0 else None
        end_to_end_ratio = control_end_to_end / treatment if treatment > 0 else None
        treatment_ok = all(row["treatment"]["outcome"] == proto["expected_treatment"] for row in rows)
        scored = all(row["treatment"].get("scored", False) for row in rows)
        control_ok = all(row["control"]["outcome"] == proto["expected_control"] for row in rows)
        probe_rows.append({
            "id": probe_id, "category": proto["category"],
            "expected_treatment": proto["expected_treatment"],
            "expected_control": proto["expected_control"],
            "treatment_median_ms": treatment, "control_median_ms": control,
            "control_end_to_end_median_ms": control_end_to_end,
            "latency_ratio_control_over_treatment": ratio,
            "end_to_end_latency_ratio_control_over_treatment": end_to_end_ratio,
            "treatment_expected_outcome": treatment_ok,
            "control_expected_outcome": control_ok,
            "scored": scored,
        })
    violations = [row for row in probe_rows if row["expected_treatment"] == "blocked"]
    negatives = [row for row in probe_rows if row["expected_treatment"] == "allowed"]
    caught = sum(row["treatment_expected_outcome"] for row in violations)
    false_blocks = sum(not row["treatment_expected_outcome"] for row in negatives)
    ratios = [row["latency_ratio_control_over_treatment"] for row in probe_rows if row["latency_ratio_control_over_treatment"] is not None]
    end_to_end_ratios = [row["end_to_end_latency_ratio_control_over_treatment"] for row in probe_rows if row["end_to_end_latency_ratio_control_over_treatment"] is not None]
    return {
        "schema_version": 1,
        "kind": "deterministic_feedback_timing_mechanism_study",
        "symbolic_fallback": symbolic_fallback,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "configuration": config,
        "repeats": repeats,
        "probes": probe_rows,
        "summary": {
            "violations": len(violations), "violations_caught": caught,
            "negative_controls": len(negatives), "false_blocks": false_blocks,
            "median_latency_ratio_control_over_treatment": _median(ratios),
            "median_end_to_end_latency_ratio_control_over_treatment": _median(end_to_end_ratios),
            "all_expected_outcomes": all(row["treatment_expected_outcome"] and row["control_expected_outcome"] for row in probe_rows),
            "all_treatment_calls_scored": all(row["scored"] for row in probe_rows),
        },
        "limitations": [
            "Invokes the pre-edit hook directly; it does not measure a specific agent host's hook-delivery latency.",
            "Measures deterministic fixture probes, not agent task success or regression reduction.",
            "Control command choice is probe-specific; queued CI and PR review latency are out of scope.",
            "Default SSM scoring is required for every hook call unless symbolic fallback is explicitly marked in this report; SSM causality is not inferred from this study.",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = ["# Feedback Timing Pilot", "", "Mechanism study only; not a quality or regression outcome study.", ""]
    lines += [f"- Violations caught: {summary['violations_caught']}/{summary['violations']}"]
    lines += [f"- False blocks: {summary['false_blocks']}/{summary['negative_controls']}"]
    lines += [f"- Median post-write-check/treatment latency ratio: {summary['median_latency_ratio_control_over_treatment']:.2f}x"]
    lines += [f"- Median end-to-end-control/treatment latency ratio: {summary['median_end_to_end_latency_ratio_control_over_treatment']:.2f}x"]
    lines += [f"- All treatment calls scored by sidecar: {'yes' if summary['all_treatment_calls_scored'] else 'no'}"]
    if report["symbolic_fallback"]:
        lines += ["- WARNING: symbolic fallback was enabled; this run cannot support an SSM timing claim."]
    lines += ["", "| Probe | Category | Treatment ms | Post-write check ms | End-to-end control ms | Check/treatment | Expected outcomes |", "|---|---|---:|---:|---:|---:|---|"]
    for row in report["probes"]:
        ratio = row["latency_ratio_control_over_treatment"]
        expected = "yes" if row["treatment_expected_outcome"] and row["control_expected_outcome"] else "no"
        lines.append(f"| {row['id']} | {row['category']} | {row['treatment_median_ms']:.1f} | {row['control_median_ms']:.1f} | {row['control_end_to_end_median_ms']:.1f} | {ratio:.2f}x | {expected} |")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--sidecar-url", default=os.environ.get("S2_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--allow-symbolic-fallback", action="store_true",
                        help="smoke-test only; marks the report non-eligible for Mamba timing claims")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")

    probes = _load_probes(args.probes)
    health = _sidecar_health(args.sidecar_url)
    if not args.allow_symbolic_fallback and not (health and health.get("model_loaded") is True):
        parser.error(
            f"Mamba sidecar is not ready at {args.sidecar_url}; start it and wait for model_loaded=true, "
            "or use --allow-symbolic-fallback for a non-claimable smoke test"
        )
    out_dir = args.out_dir or (ROOT / "eval" / "runs" / f"timing-pilot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    out_dir.mkdir(parents=True, exist_ok=False)
    config = {
        "code_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        "probes_sha256": _sha256(args.probes),
        "python": sys.version,
        "hook": str(HOOK),
        "neural_policy": "RC_NEURAL_CORROBORATED=1",
        "execution_surface": "direct_pre_edit_guard_subprocess_not_agent_host_e2e",
        "sidecar_url": args.sidecar_url,
        "sidecar_health": health,
    }
    (out_dir / "manifest.json").write_text(json.dumps({"probes": probes, "configuration": config}, indent=2) + "\n", encoding="utf-8")

    all_rows: list[dict[str, Any]] = []
    for index in range(args.repeats):
        for probe in probes:
            with tempfile.TemporaryDirectory(prefix="rc-timing-pilot-") as temp:
                project = Path(temp) / "project"
                project.mkdir()
                subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
                target = _write_fixture(project, probe)
                audit_root = Path(temp) / "audit"
                treatment = _run_treatment(
                    project, target, probe, audit_root, args.sidecar_url,
                    require_ssm=not args.allow_symbolic_fallback,
                )
            with tempfile.TemporaryDirectory(prefix="rc-timing-pilot-") as temp:
                project = Path(temp) / "project"
                project.mkdir()
                subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
                target = _write_fixture(project, probe)
                control = _run_control(project, target, probe)
            all_rows.append({"repeat": index + 1, "probe_id": probe["id"], "category": probe["category"], "expected_treatment": probe["expected_treatment"], "expected_control": probe["expected_control"], "treatment": treatment, "control": control})

    (out_dir / "raw.jsonl").write_text("".join(json.dumps(row) + "\n" for row in all_rows), encoding="utf-8")
    report = _report(all_rows, repeats=args.repeats, config=config, symbolic_fallback=args.allow_symbolic_fallback)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(_markdown(report), encoding="utf-8")
    print(f"[timing-pilot] wrote {out_dir / 'report.md'}")
    valid = report["summary"]["all_expected_outcomes"]
    if not args.allow_symbolic_fallback:
        valid = valid and report["summary"]["all_treatment_calls_scored"]
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
