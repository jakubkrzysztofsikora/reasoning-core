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


def cmd_status(_args: argparse.Namespace) -> int:
    sys.stdout.write("== reasoning-core status ==\n\nenv knobs:\n")
    for k in _KNOBS:
        _print_kv(k, os.environ.get(k, "<unset>"))
    sys.stdout.write("\nkill switches:\n")
    snap = ks.snapshot()
    _print_kv("bypass_next", str(snap.get("bypass_next", False)))
    _print_kv("skip_files", str(snap.get("skip_files", [])))
    _print_kv("disable_until", str(snap.get("disable_until")))
    sys.stdout.write("\naudit log:\n")
    _print_kv("root", str(_audit_root()))
    today = _today_dir()
    if today.exists():
        files = sorted(today.glob("*.jsonl"))
        _print_kv("today_files", str(len(files)))
    else:
        _print_kv("today_files", "0 (no events today)")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    target = args.decision_id
    today = _today_dir()
    if not today.exists():
        sys.stderr.write(f"no audit dir for today at {today}\n")
        return 1
    for jf in today.glob("*.jsonl"):
        try:
            with open(jf, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("decision_id") == target:
                        sys.stdout.write(json.dumps(row, indent=2) + "\n")
                        return 0
        except OSError:
            continue
    sys.stderr.write(f"decision_id {target} not found in {today}\n")
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
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
