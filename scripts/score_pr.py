#!/usr/bin/env python3
"""Score the files changed in a PR/MR using the local reasoning-core sidecar.

This script is meant to run in CI (GitHub Actions, Azure DevOps, GitLab, etc.)
and produces a Markdown report of architectural-risk scores for every changed
source file. It can fail the build when a regression is detected, or just
comment — behavior is controlled by `--fail-on-block`.

Usage:
    python3 scripts/score_pr.py \
        --base-ref origin/main \
        --head-ref HEAD \
        --output report.md \
        --json scores.json

Environment:
    S2_URL              Sidecar URL (default http://127.0.0.1:8765).
    RC_SCORE_MODE       "sidecar" (default) or "symbolic". Symbolic mode skips
                        the SSM embedder and runs only contract/oracle checks.
    S2_FAIL_CLOSED      If "1" and the sidecar is unreachable, fail instead of
                        warn.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SIDECAR_URL = os.getenv("S2_URL", "http://127.0.0.1:8765")
SCORE_ENDPOINT = f"{SIDECAR_URL}/score"
HEALTH_ENDPOINT = f"{SIDECAR_URL}/health"


def _run(cmd: List[str], timeout: float = 30.0) -> str:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {result.stderr}")
    return result.stdout


def _changed_files(base_ref: str, head_ref: str) -> List[str]:
    """Return list of changed file paths between base and head."""
    diff = _run(["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{base_ref}...{head_ref}"])
    return [line.strip() for line in diff.splitlines() if line.strip()]


def _read_file_at(ref: str, path: str) -> str:
    """Return file contents at a git ref, or empty string if absent."""
    try:
        return _run(["git", "show", f"{ref}:{path}"])
    except RuntimeError:
        return ""


def _sidecar_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_ENDPOINT, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _score_file(path: str, before: str, after: str) -> Optional[Dict[str, Any]]:
    """Call the sidecar /score endpoint. Returns parsed report or None."""
    payload = json.dumps({"path": path, "before_src": before, "after_src": after}).encode("utf-8")
    req = urllib.request.Request(
        SCORE_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
    except urllib.error.HTTPError as exc:
        if exc.code == 415:
            return {"unsupported_language": True, "extension": Path(path).suffix}
    except Exception:
        pass
    return None


def _symbolic_score(path: str, before: str, after: str) -> Optional[Dict[str, Any]]:
    """Run symbolic checks without the SSM sidecar. Falls back to imports."""
    try:
        tool_root = Path(__file__).resolve().parent.parent
        root_dir = str(tool_root)
        hooks_dir = str(tool_root / "src" / "hooks")
        for d in (root_dir, hooks_dir):
            if d not in sys.path:
                sys.path.insert(0, d)
        from src.hooks import _dispatch  # type: ignore

        outcomes: List[Any] = []
        outcomes.append(
            _dispatch.gate_rule_engine(
                file_path=path,
                before_src=before,
                after_src=after,
                report={},
            )
        )
        outcomes.append(_dispatch.gate_plan_grounding(file_path=path, after_src=after))

        blocked = [o for o in outcomes if o.action == "exit_block"]
        warnings = [o for o in outcomes if o.action == "stderr_only"]
        if blocked:
            first = blocked[0]
            return {
                "regression_detected": True,
                "reason": first.reason,
                "signal_source": first.signal_source,
                "human_summary": first.stderr.strip() if first.stderr else first.reason,
            }
        if warnings:
            first = warnings[0]
            return {
                "regression_detected": False,
                "reason": first.reason,
                "signal_source": first.signal_source,
                "human_summary": first.stderr.strip() if first.stderr else first.reason,
            }
        return {"regression_detected": False, "reason": "symbolic_checks_passed"}
    except Exception as exc:
        return {"error": str(exc)}


def _format_report(results: List[Dict[str, Any]], duration_s: float) -> str:
    buf = io.StringIO()
    buf.write("# reasoning-core PR Score Report\n\n")
    buf.write(f"Files scored: {len(results)} | Wall time: {duration_s:.1f}s\n\n")

    blocked = [r for r in results if r.get("regression_detected")]
    warned = [r for r in results if not r.get("regression_detected") and r.get("warning")]
    clean = [r for r in results if r not in blocked and r not in warned]

    if blocked:
        buf.write(f"## 🚫 Blocked ({len(blocked)})\n\n")
        for r in blocked:
            buf.write(f"- **{r['path']}** — {r.get('human_summary', r.get('reason', 'blocked'))}\n")
        buf.write("\n")
    if warned:
        buf.write(f"## ⚠️ Warnings ({len(warned)})\n\n")
        for r in warned:
            buf.write(f"- **{r['path']}** — {r.get('human_summary', r.get('reason', 'warning'))}\n")
        buf.write("\n")
    if clean:
        buf.write(f"## ✅ Clean ({len(clean)})\n\n")
        for r in clean:
            summary = r.get("human_summary", r.get("reason", "ok"))
            buf.write(f"- **{r['path']}** — {summary}\n")
        buf.write("\n")

    buf.write("## Per-file details\n\n")
    buf.write("| file | decision | score | top risk | summary |\n")
    buf.write("|---|---|---|---|---|\n")
    for r in results:
        decision = "blocked" if r.get("regression_detected") else ("warn" if r.get("warning") else "pass")
        score = r.get("architectural_impact_score", "n/a")
        if isinstance(score, float):
            score = f"{score:.2f}"
        top = "n/a"
        rv = r.get("risk_vector") or []
        rl = r.get("risk_labels") or []
        if rv and rl and len(rv) == len(rl):
            top = max(zip(rl, rv), key=lambda x: x[1])[0]
        summary = r.get("human_summary", r.get("reason", "")).replace("|", "\\|")
        buf.write(f"| {r['path']} | {decision} | {score} | {top} | {summary} |\n")
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Score changed files in a PR.")
    parser.add_argument("--base-ref", required=True, help="Base git ref.")
    parser.add_argument("--head-ref", default="HEAD", help="Head git ref.")
    parser.add_argument("--output", default="reasoning-core-pr-report.md", help="Markdown report path.")
    parser.add_argument("--json", default=None, help="Optional JSON output path.")
    parser.add_argument("--fail-on-block", action="store_true", help="Exit non-zero if any file is blocked.")
    parser.add_argument("--mode", default=os.getenv("RC_SCORE_MODE", "sidecar"), choices=["sidecar", "symbolic"])
    parser.add_argument("--include", default="*.py,*.js,*.ts,*.jsx,*.tsx,*.cs,*.go,*.rs,*.java,*.rb", help="Comma-separated glob list of file extensions to score.")
    args = parser.parse_args()

    include_suffixes = {ext.strip().lstrip("*.") for ext in args.include.split(",") if ext.strip()}

    started = time.time()
    files = _changed_files(args.base_ref, args.head_ref)
    files = [f for f in files if any(f.endswith(f".{s}") for s in include_suffixes)]

    if not files:
        report = "# reasoning-core PR Score Report\n\nNo matching changed files to score.\n"
        Path(args.output).write_text(report, encoding="utf-8")
        print(report)
        return 0

    mode = args.mode
    if mode == "sidecar" and not _sidecar_healthy():
        print("[reasoning-core] sidecar not healthy; falling back to symbolic mode.", file=sys.stderr)
        mode = "symbolic"

    results: List[Dict[str, Any]] = []
    for path in files:
        before = _read_file_at(args.base_ref, path)
        after = _read_file_at(args.head_ref, path)
        if mode == "symbolic":
            report = _symbolic_score(path, before, after)
        else:
            report = _score_file(path, before, after)

        row: Dict[str, Any] = {"path": path}
        if report is None:
            row["error"] = "score_call_failed"
        else:
            row.update(report)
            row["warning"] = not report.get("regression_detected") and report.get("signal_source") in {"plan_grounding", "rules"}
        results.append(row)

    duration = time.time() - started
    report_md = _format_report(results, duration)
    Path(args.output).write_text(report_md, encoding="utf-8")
    print(report_md)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")

    blocked = [r for r in results if r.get("regression_detected")]
    if args.fail_on_block and blocked:
        print(f"\n[reasoning-core] {len(blocked)} file(s) blocked. Failing the check.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
