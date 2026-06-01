"""Reasoning-core operator CLI. Day-zero ergonomics per plan v2 P-1.

Subcommands:
    rc status                 — env knobs + sidecar health + last 5 decisions
    rc explain <decision-id>  — full audit row for a decision
    rc bypass-next            — arm a one-shot bypass (consumed on next hook call)
    rc skip-file <path>       — add file to per-session skip list
    rc unskip-file <path>     — remove file from skip list

Reads the same kill-switch file as src/hooks/_kill_switches.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make hook helpers importable without installing the package.
_HOOKS_DIR = Path(__file__).resolve().parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _kill_switches as ks  # type: ignore  # noqa: E402

_KNOBS = (
    "S2_DEVICE", "S2_TIMEOUT", "S2_FAIL_CLOSED", "S2_PORT",
    "S2_AIS_THRESHOLD", "S2_COHERENCE_THRESHOLD", "S2_RISK_DIM_THRESHOLD",
    "RC_PLAN_BLOCK", "RC_ALLOW_GUARD_EDIT", "RC_ALLOW_SUBAGENT_GUARD_EDIT",
    "RC_LANG_OVERRIDE", "RC_LANG_ALLOW", "RC_DRIFT_OVERRIDE",
    "RC_MOCK_DETECTOR", "RC_PLAN_QUALITY", "RC_LANG_LOCK",
    "RC_SHADOW_MODE", "RC_REASONER_BACKEND", "RC_GEN_BUDGET_MS",
    "RC_GEN_MODEL", "RC_GEN_URL",
    "RC_CALIBRATION_ENABLED", "RC_RECALIBRATE_POLL_S",
    "RC_BYPASS_NEXT",
)


def _audit_root() -> Path:
    return Path(os.environ.get(
        "RC_AUDIT_ROOT",
        os.path.expanduser("~/.local/share/reasoning-core/events"),
    ))


def _today_dir() -> Path:
    import datetime as dt
    return _audit_root() / dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _print_kv(k: str, v: str) -> None:
    sys.stdout.write(f"  {k:<32} {v}\n")


def _calibration_status() -> dict:
    """Read calibration sentinel files for `rc status` block.

    Looks at: eval/runs/calibration.json (Mahalanobis model),
              eval/runs/qwen_kappa_gate.json (CDGS gate),
              eval/runs/recalibrate.signal (pending refit).
    """
    import time
    repo = Path(__file__).resolve().parent.parent
    runs = repo / "eval" / "runs"
    out: dict = {}
    calib_path = runs / "calibration.json"
    if calib_path.exists():
        try:
            d = json.loads(calib_path.read_text())
            out["calibration_threshold"] = d.get("threshold")
            ci = d.get("threshold_ci95")
            if ci:
                out["calibration_ci_width"] = round(ci[1] - ci[0], 4)
            out["calibration_n"] = d.get("n")
            out["calibration_mtime_age_h"] = round(
                (time.time() - calib_path.stat().st_mtime) / 3600, 1
            )
        except (OSError, ValueError):
            out["calibration"] = "<corrupt>"
    else:
        out["calibration"] = "<not fitted>"

    kappa_path = runs / "qwen_kappa_gate.json"
    if kappa_path.exists():
        try:
            d = json.loads(kappa_path.read_text())
            out["qwen_kappa"] = round(d.get("kappa", 0.0), 3)
            out["qwen_gate_pass"] = d.get("gate_pass")
        except (OSError, ValueError):
            out["qwen_kappa"] = "<corrupt>"
    else:
        out["qwen_kappa"] = "<not run>"

    signal_path = runs / "recalibrate.signal"
    out["recalibrate_signal"] = "PENDING" if signal_path.exists() else "none"
    return out


def cmd_status(_args: argparse.Namespace) -> int:
    sys.stdout.write("== reasoning-core status ==\n\nenv knobs:\n")
    for k in _KNOBS:
        _print_kv(k, os.environ.get(k, "<unset>"))
    sys.stdout.write("\nkill switches:\n")
    snap = ks.snapshot()
    _print_kv("bypass_next", str(snap.get("bypass_next", False)))
    _print_kv("skip_files", str(snap.get("skip_files", [])))
    _print_kv("disable_until", str(snap.get("disable_until")))
    sys.stdout.write("\ncalibration:\n")
    for k, v in _calibration_status().items():
        _print_kv(k, str(v))
    sys.stdout.write("\naudit log:\n")
    _print_kv("root", str(_audit_root()))
    today = _today_dir()
    if today.exists():
        files = sorted(today.glob("*.jsonl"))
        _print_kv("today_files", str(len(files)))
    else:
        _print_kv("today_files", "0 (no events today)")
    return 0


def _open_log(path: Path):
    """Open .jsonl or .jsonl.gz transparently. Returns text-mode handle."""
    import gzip
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _scan_log(path: Path, target: str):
    try:
        with _open_log(path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("decision_id") == target:
                    return row
    except OSError:
        return None
    return None


def cmd_explain(args: argparse.Namespace) -> int:
    target = args.decision_id
    root = _audit_root()
    if not root.exists():
        sys.stderr.write(f"no audit root at {root}\n")
        return 1
    # Walk newest-day-first so today's hits return fast.
    for day_dir in sorted(root.iterdir(), reverse=True):
        if not day_dir.is_dir():
            continue
        for log in list(day_dir.glob("*.jsonl")) + list(day_dir.glob("*.jsonl.gz")):
            row = _scan_log(log, target)
            if row is not None:
                sys.stdout.write(json.dumps(row, indent=2) + "\n")
                return 0
    sys.stderr.write(f"decision_id {target} not found under {root}\n")
    return 1


def cmd_bypass_next(_args: argparse.Namespace) -> int:
    ks.set_bypass_next(True)
    sys.stdout.write("bypass_next armed (consumed on next PreToolUse hook call)\n")
    return 0


def cmd_skip_file(args: argparse.Namespace) -> int:
    ks.add_skip_file(os.path.abspath(args.path))
    sys.stdout.write(f"added: {os.path.abspath(args.path)}\n")
    return 0


def cmd_unskip_file(args: argparse.Namespace) -> int:
    ks.remove_skip_file(os.path.abspath(args.path))
    sys.stdout.write(f"removed: {os.path.abspath(args.path)}\n")
    return 0


# --- reasoning-efficiency (audit 2026-06-01 §7 north-star metric) -----------
# Composite: (drift_caught - false_drifts) / (gate_wall_clock_s + 1)
#             * repo_idiom_adherence_delta_norm * (1 - sidecar_unavailability_rate)
# repo_idiom_adherence_delta_norm = 0.43 (iter-3 measured value, frozen until
# a live measurement lands). The audit log already carries every input.
_REPO_IDIOM_DELTA_NORM = 0.43


def _walk_audit_events(audit_root: Path, days: int):
    import datetime as _dt
    import gzip
    cutoff = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=days)
    if not audit_root.is_dir():
        return
    for day_dir in sorted(audit_root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")):
        try:
            day = _dt.datetime.strptime(day_dir.name, "%Y-%m-%d").replace(
                tzinfo=_dt.timezone.utc
            )
        except ValueError:
            continue
        if day < cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        for f in day_dir.iterdir():
            opener = gzip.open if f.name.endswith(".gz") else open
            try:
                with opener(f, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except (ValueError, TypeError):
                            continue
            except OSError:
                continue


def cmd_reasoning_efficiency(args: argparse.Namespace) -> int:
    """Audit 2026-06-01 §7: composite north-star metric from the audit log."""
    audit_root = Path(args.audit_root or _audit_root())
    drift_caught = 0
    false_drifts = 0
    total_latency_ms = 0
    sidecar_unavailable = 0
    n_events = 0
    for ev in _walk_audit_events(audit_root, args.days):
        n_events += 1
        latency = ev.get("latency_ms")
        if isinstance(latency, int):
            total_latency_ms += latency
        reason = ev.get("reason") or ""
        decision = ev.get("decision") or ""
        if reason == "plan_impl_drift" and decision in ("blocked", "warn", "shadow_blocked"):
            drift_caught += 1
            if ev.get("retry_after_block") is True and decision == "blocked":
                # Proxy for false-drift: agent retried and the retry was allowed.
                false_drifts += 1
        if isinstance(reason, str) and reason.startswith("sidecar_unavailable"):
            sidecar_unavailable += 1

    if n_events == 0:
        print(f"no events in last {args.days} days under {audit_root}")
        return 0
    gate_wall_clock_s = total_latency_ms / 1000.0
    sidecar_unavailable_rate = sidecar_unavailable / n_events
    numerator = max(0, drift_caught - false_drifts)
    eff = (
        (numerator / (gate_wall_clock_s + 1.0))
        * _REPO_IDIOM_DELTA_NORM
        * max(0.0, 1.0 - sidecar_unavailable_rate)
    )
    _print_kv("days", str(args.days))
    _print_kv("events", str(n_events))
    _print_kv("drift_caught", str(drift_caught))
    _print_kv("false_drifts (proxy)", str(false_drifts))
    _print_kv("gate_wall_clock_s", f"{gate_wall_clock_s:.2f}")
    _print_kv("sidecar_unavailable_rate", f"{sidecar_unavailable_rate:.3f}")
    _print_kv("repo_idiom_delta_norm (const)", f"{_REPO_IDIOM_DELTA_NORM}")
    _print_kv("reasoning_efficiency", f"{eff:.6f}")
    return 0


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="rc", description="reasoning-core operator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    e = sub.add_parser("explain")
    e.add_argument("decision_id")
    e.set_defaults(func=cmd_explain)
    sub.add_parser("bypass-next").set_defaults(func=cmd_bypass_next)
    s = sub.add_parser("skip-file")
    s.add_argument("path")
    s.set_defaults(func=cmd_skip_file)
    u = sub.add_parser("unskip-file")
    u.add_argument("path")
    u.set_defaults(func=cmd_unskip_file)
    re_cmd = sub.add_parser(
        "reasoning-efficiency",
        help="audit-log composite metric (audit 2026-06-01 §7)",
    )
    re_cmd.add_argument("--days", type=int, default=7)
    re_cmd.add_argument("--audit-root", default=None)
    re_cmd.set_defaults(func=cmd_reasoning_efficiency)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
