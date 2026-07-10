"""Reasoning-core operator CLI. Day-zero ergonomics per plan v2 P-1.

Subcommands:
    rc status                 — env knobs + sidecar health + last 5 decisions
    rc explain <decision-id>  — full audit row for a decision
    rc bypass-next            — arm a one-shot bypass (consumed on next hook call)
                              emits an operator_override audit event
    rc confirm-next           — record operator agreement with a block
                              emits an operator_confirmed audit event
    rc skip-file <path>       — add file to per-session skip list
    rc unskip-file <path>     — remove file from skip list
    rc score-pr               — score changed files in a PR/MR (CI helper)

Reads the same kill-switch file as src/hooks/_kill_switches.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Make hook helpers importable without installing the package.
_HOOKS_DIR = Path(__file__).resolve().parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

import _commit_miner as _cm  # type: ignore  # noqa: E402
import _kill_switches as ks  # type: ignore  # noqa: E402
import audit_log  # type: ignore  # noqa: E402

_KNOBS = (
    "S2_DEVICE", "S2_TIMEOUT", "S2_FAIL_CLOSED", "S2_PORT",
    "S2_AIS_THRESHOLD", "S2_COHERENCE_THRESHOLD", "S2_RISK_DIM_THRESHOLD",
    "RC_MODE", "RC_PLAN_BLOCK", "RC_ALLOW_GUARD_EDIT", "RC_ALLOW_SUBAGENT_GUARD_EDIT",
    "RC_LANG_OVERRIDE", "RC_LANG_ALLOW", "RC_DRIFT_OVERRIDE",
    "RC_MOCK_DETECTOR", "RC_PLAN_QUALITY", "RC_LANG_LOCK",
    "RC_SHADOW_MODE", "RC_REASONER_BACKEND", "RC_GEN_BUDGET_MS",
    "RC_GEN_MODEL", "RC_GEN_URL",
    "RC_CALIBRATION_ENABLED", "RC_RECALIBRATE_POLL_S",
    "RC_BEST_EFFORT_SPEC", "RC_PLAN_GROUNDING", "RC_RULE_ENGINE",
    "RC_ORACLE_T1", "RC_ORACLE_T2", "RC_ORACLE_BLOCK",
    "RC_PRM_GATE", "RC_PRM_BLOCK", "RC_PRM_THRESHOLD",
    "RC_PRM_PROMO_MIN_REPOS", "RC_PRM_PROMO_MIN_EVENTS", "RC_PRM_PROMO_MIN_DAYS",
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
    audit_log.record_operator_override(reason="bypass_next_armed")
    sys.stdout.write("bypass_next armed (consumed on next PreToolUse hook call)\n")
    return 0


def cmd_confirm_next(_args: argparse.Namespace) -> int:
    audit_log.record_operator_confirmed(reason="confirm_next_armed")
    sys.stdout.write("confirm_next recorded (operator agrees the next block was correct)\n")
    return 0


def cmd_skip_file(args: argparse.Namespace) -> int:
    ks.add_skip_file(os.path.abspath(args.path))
    sys.stdout.write(f"added: {os.path.abspath(args.path)}\n")
    return 0


def cmd_unskip_file(args: argparse.Namespace) -> int:
    ks.remove_skip_file(os.path.abspath(args.path))
    sys.stdout.write(f"removed: {os.path.abspath(args.path)}\n")
    return 0


def cmd_score_pr(args: argparse.Namespace) -> int:
    """Score the files changed between two git refs using scripts/score_pr.py.

    This is the local / CI entry point for the PR scorer. It shells out to
    ``scripts/score_pr.py`` so the same implementation runs everywhere.
    """
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "score_pr.py"
    if not script.is_file():
        sys.stderr.write(f"score_pr.py not found at {script}\n")
        return 1

    cmd = [
        sys.executable,
        str(script),
        "--base-ref", args.base_ref,
        "--head-ref", args.head_ref,
        "--mode", args.mode,
    ]
    if args.include:
        cmd += ["--include", args.include]
    if args.output:
        cmd += ["--output", args.output]
    if args.json:
        cmd += ["--json", args.json]
    if args.fail_on_block:
        cmd += ["--fail-on-block"]

    result = subprocess.run(cmd, cwd=str(repo_root), check=False)
    return result.returncode


def _project_dir() -> Path:
    """Resolve the repo/project directory the gate is watching."""
    return Path(
        os.environ.get("RC_RUN_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )


def _update_envrc_local(project_dir: Path) -> Path:
    """Idempotently append/patch a reasoning-core enforcement block."""
    envrc_local = project_dir / ".envrc.local"
    sentinel_start = "# >>> reasoning-core enforcement >>>"
    sentinel_end = "# <<< reasoning-core enforcement <<<"
    block = f"""{sentinel_start}
# Enabled via `rc enable-enforcement`. Copilot mode: block on
# contract/oracle/rule failures. Autopilot remains opt-in.
export RC_MODE=copilot
export RC_SHADOW_MODE=0
export RC_PLAN_BLOCK=1
# Keep plan-grounding warn-only unless you want hard blocks:
# export RC_PLAN_GROUNDING=2
{sentinel_end}
"""
    existing = ""
    if envrc_local.exists():
        existing = envrc_local.read_text(encoding="utf-8")
        # Strip any previous reasoning-core enforcement block.
        import re
        pattern = re.compile(
            rf"\n?{re.escape(sentinel_start)}.*?{re.escape(sentinel_end)}\n?",
            re.DOTALL,
        )
        existing = pattern.sub("\n", existing).strip("\n")
        if existing:
            existing += "\n"
    envrc_local.write_text(existing + block, encoding="utf-8")
    return envrc_local


def cmd_enable_enforcement(_args: argparse.Namespace) -> int:
    """First-run wizard: scaffold PLAN.md and flip repo to copilot mode."""
    project_dir = _project_dir()
    if not project_dir.is_dir():
        sys.stderr.write(f"project directory does not exist: {project_dir}\n")
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    try:
        import _plan_scaffold as ps  # type: ignore  # noqa: E402
    except Exception as exc:
        sys.stderr.write(f"could not load plan scaffold helper: {exc}\n")
        return 1

    plan_path = ps.maybe_scaffold_plan(str(project_dir))
    envrc_local = _update_envrc_local(project_dir)

    sys.stdout.write("== reasoning-core enforcement enabled ==\n\n")
    if plan_path:
        sys.stdout.write(f"scaffolded plan: {plan_path}\n")
    else:
        sys.stdout.write(
            "PLAN.md already exists or no README.md found; not scaffolding.\n"
        )
    sys.stdout.write(f"wrote enforcement config: {envrc_local}\n\n")
    sys.stdout.write("modes:\n")
    sys.stdout.write("  advise    (default) — log/warn only, never block\n")
    sys.stdout.write("  copilot   — block on contract/oracle/rule failures\n")
    sys.stdout.write("  autopilot — block + auto-repair within policy (opt-in)\n\n")
    sys.stdout.write("next:\n")
    sys.stdout.write("  direnv reload      # or: source .envrc.local\n")
    sys.stdout.write("  rc status          # confirm RC_MODE=copilot\n")
    return 0


# --- reasoning-efficiency (audit 2026-06-01 §7 north-star metric) -----------
# Composite: (drift_caught - false_drifts) / (gate_wall_clock_s + 1)
#             * repo_idiom_adherence_delta_norm * (1 - sidecar_unavailability_rate)
# repo_idiom_adherence_delta_norm = 0.43 (iter-3 measured value, frozen until
# a live measurement lands). The audit log already carries every input.
_REPO_IDIOM_DELTA_NORM = 0.43



def _load_override_links() -> dict[str, set[str]]:
    """Load override_links.json, return {file_path: {blocked_decision_id, ...}}."""
    import json as _json
    state_dir = os.path.expanduser("~/.local/state/reasoning-core")
    path = os.path.join(state_dir, "override_links.json")
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                links = _json.loads(fh.read() or "{}")
        else:
            return {}
    except (OSError, ValueError):
        return {}
    result: dict[str, set[str]] = {}
    for v in links.values():
        if isinstance(v, dict):
            fp = v.get("file_path")
            bid = v.get("blocked_decision_id")
            if fp and bid:
                result.setdefault(fp, set()).add(bid)
    return result


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
    override_links = _load_override_links()
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
            # Check retry_after_block proxy
            if ev.get("retry_after_block") is True and decision == "blocked":
                false_drifts += 1
            # Check override_links: if block was later overridden, count as false
            elif ev.get("file_path") and ev.get("decision_id"):
                fp_overrides = override_links.get(ev["file_path"], set())
                if ev["decision_id"] in fp_overrides:
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



def _git_repo_root() -> Path | None:
    """Best-effort current git repo root. Returns None outside a repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:
        pass
    return None


def _override_survival_counts(audit_root: Path, days: int, repo_root: Path | None) -> tuple[int, int, int]:
    """Return (survived, reverted, unknown) for allowed_via_override events.

    Shared by ``cmd_override_survival`` and ``cmd_benchmark`` so the survival
    calculation stays consistent and the repo-root guard is not duplicated.
    """
    survived = 0
    reverted = 0
    unknown = 0
    for ev in _walk_audit_events(audit_root, days):
        if ev.get("decision") != "allowed_via_override":
            continue
        extra = ev.get("extra")
        git_head = extra.get("git_head") if isinstance(extra, dict) else ev.get("git_head")
        fp = ev.get("file_path")
        if not git_head or not fp:
            unknown += 1
            continue
        if repo_root is None:
            unknown += 1
            continue
        try:
            r = subprocess.run(
                ["git", "show", f"{git_head}:{fp}"],
                capture_output=True, text=True, timeout=5, cwd=str(repo_root),
            )
            content_at_override = r.stdout if r.returncode == 0 else None
        except Exception:
            content_at_override = None
        fpath = repo_root / fp
        try:
            content_current = fpath.read_text(encoding="utf-8", errors="replace") if fpath.is_file() else None
        except OSError:
            content_current = None
        if content_at_override is not None and content_current is not None:
            if content_at_override == content_current:
                survived += 1
            else:
                reverted += 1
        else:
            unknown += 1
    return survived, reverted, unknown


def cmd_override_survival(args: argparse.Namespace) -> int:
    '''Compute override survival ratio from audit log git_head fields.'''
    audit_root = Path(args.audit_root or _audit_root())

    repo_root = _git_repo_root()
    survived, reverted, unknown = _override_survival_counts(audit_root, args.days, repo_root)
    total = survived + reverted + unknown
    if total == 0:
        print(f"no override events with git_head in last {args.days} days under {audit_root}")
        return 0
    _print_kv("total overrides", str(total))
    _print_kv("survived (unchanged)", str(survived))
    _print_kv("reverted (changed)", str(reverted))
    _print_kv("unknown", str(unknown))
    _print_kv("survival ratio", f"{survived / max(1, total):.2%}")
    _print_kv("reverted ratio", f"{reverted / max(1, total):.2%}")
    return 0



def cmd_audit_history(args: argparse.Namespace) -> int:
    """Mine recent git history and print per-commit quality labels.

    Labels commits as positive/negative based on whether they were followed
    within 48 hours by a fix/revert/hotfix/patch touching the same files.
    This is the feedback loop input for Phase-4 calibration.
    """
    project_dir = Path(args.project_dir) if args.project_dir else _project_dir()
    if not project_dir.is_dir():
        sys.stderr.write(f"project directory does not exist: {project_dir}\n")
        return 1

    try:
        commits = _cm.mine(str(project_dir), n=args.n)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"could not mine commits: {exc}\n")
        return 1

    if not commits:
        sys.stdout.write(f"no commits mined under {project_dir}\n")
        return 0

    if args.json:
        sys.stdout.write(json.dumps([c.to_dict() for c in commits], indent=2) + "\n")
        return 0

    sys.stdout.write(f"{'label':<9} {'sha':<9} {'date':<20} {'files':>5} {'lines':>5}  {'message'}\n")
    sys.stdout.write("-" * 80 + "\n")
    for c in commits:
        date_str = c.date.strftime("%Y-%m-%d %H:%M") if c.date else ""
        msg = (c.message or "")[:50]
        sys.stdout.write(
            f"{c.label or 'unknown':<9} {c.sha[:7]:<9} {date_str:<20} "
            f"{len(c.files):>5} {sum(c.diff_stat.values()):>5}  {msg}\n"
        )
        if args.reasons and c.label_reason:
            sys.stdout.write(f"          reason: {c.label_reason}\n")
    return 0


# --- benchmark (audit schema v4 measurement foundation) ----------------------


def _classify_severity(reason: str, decision: str, gate_id: str | None) -> str:
    """Map an audit event to a severity/layer class for the benchmark report."""
    if not isinstance(reason, str):
        reason = ""
    reason_l = reason.lower()
    decision_l = str(decision).lower()

    if decision_l == "fail-open":
        return "fail_open"
    if decision_l == "shadow_blocked":
        return "shadow"
    if decision_l in ("operator_override", "allowed_via_override"):
        return "override"

    if "contract_violation" in reason_l:
        return "contract"
    if reason_l.startswith("oracle_") or "oracle" in reason_l:
        return "oracle"
    if reason_l.startswith("rule_engine:"):
        return "rule_engine"
    if reason_l == "plan_impl_drift":
        return "plan_grounding"
    if reason_l in ("shell_escape", "guard_file_locked", "language_fingerprint_violation"):
        return "self_protection"
    if reason_l.startswith("kill_switch") or reason_l.startswith("magic_comment"):
        return "self_protection"
    if gate_id == "plan_grounding" or "plan" in reason_l:
        return "plan_grounding"
    if gate_id == "rules":
        return "rule_engine"

    return "other"


def _token_cost_proxy(metrics: dict) -> float:
    """Rough proxy: every scored event costs a base prompt turn; blocks and
    overrides cost an extra retry turn.  This is intentionally conservative
    and uses fixed coefficients so the number is comparable week-over-week
    rather than an absolute dollar estimate.
    """
    base = metrics.get("total_events", 0) * 1.0
    retry = (
        metrics.get("blocked", 0)
        + metrics.get("warn", 0)
        + metrics.get("shadow_blocked", 0)
        + metrics.get("allowed_via_override", 0)
    ) * 1.5
    return round(base + retry, 1)


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _compute_benchmark(audit_root: Path, days: int, before: str | None, after: str | None) -> dict:
    """Aggregate audit events into benchmark metrics."""
    total_events = 0
    decisions: dict[str, int] = {}
    severity: dict[str, int] = {}
    latencies: list[int] = []
    block_latencies: list[int] = []
    retry_after_block = 0
    scope_creep = 0
    operator_overrides = 0
    operator_confirmed = 0
    allowed_via_override = 0
    signal_sources: dict[str, int] = {}

    for ev in _walk_audit_events(audit_root, days):
        ts = ev.get("ts")
        if before or after:
            if not isinstance(ts, str) or not ts:
                # Date-filtered windows require a timestamp; skip untimestamped
                # events so they don't contaminate both sides of a comparison.
                continue
            if before and ts[:10] >= before:
                continue
            if after and ts[:10] < after:
                continue

        total_events += 1
        decision = str(ev.get("decision") or "unknown")
        reason = str(ev.get("reason") or "")
        gate_id = ev.get("gate_id")
        decisions[decision] = decisions.get(decision, 0) + 1
        # Class counts only non-allowed decisions; otherwise "allowed" events
        # dominate the table and conflate blocked/non-blocked signals.
        if decision != "allowed":
            cls = _classify_severity(reason, decision, gate_id)
            severity[cls] = severity.get(cls, 0) + 1

        latency = ev.get("latency_ms")
        if isinstance(latency, int):
            latencies.append(latency)
            if decision == "blocked":
                block_latencies.append(latency)

        if decision == "blocked" and ev.get("retry_after_block") is True:
            retry_after_block += 1

        if reason == "plan_impl_drift" or "contract_violation" in reason.lower():
            scope_creep += 1

        if decision == "operator_override":
            operator_overrides += 1
        if decision == "operator_confirmed":
            operator_confirmed += 1
        if decision == "allowed_via_override":
            allowed_via_override += 1

        signal_source = ev.get("signal_source")
        if isinstance(signal_source, str):
            signal_sources[signal_source] = signal_sources.get(signal_source, 0) + 1

    median_latency = _percentile(latencies, 50)
    p95_latency = _percentile(latencies, 95)
    median_block_latency = _percentile(block_latencies, 50)

    metrics = {
        "total_events": total_events,
        "blocked": decisions.get("blocked", 0),
        "warn": decisions.get("warn", 0),
        "shadow_blocked": decisions.get("shadow_blocked", 0),
        "fail_open": decisions.get("fail-open", 0),
        "allowed": decisions.get("allowed", 0),
        "allowed_via_override": allowed_via_override,
        "operator_override": operator_overrides,
        "operator_confirmed": operator_confirmed,
        "retry_after_block": retry_after_block,
        "scope_creep_catches": scope_creep,
        "severity": severity,
        "signal_sources": signal_sources,
        "median_latency_ms": int(median_latency) if median_latency is not None else None,
        "p95_latency_ms": int(p95_latency) if p95_latency is not None else None,
        "median_block_latency_ms": int(median_block_latency) if median_block_latency is not None else None,
        "token_cost_proxy": _token_cost_proxy({
            "total_events": total_events,
            "blocked": decisions.get("blocked", 0),
            "warn": decisions.get("warn", 0),
            "shadow_blocked": decisions.get("shadow_blocked", 0),
            "allowed_via_override": allowed_via_override,
        }),
    }

    # False-positive proxy: count outcome signals (retries + overrides) over
    # blocking decisions.  operator_override is the arming event, not the
    # outcome, so it is intentionally excluded to avoid double-counting the
    # same incident. Clamp to 1.0 so the headline number stays interpretable
    # even when the same incident appears in both counts.
    fp_denominator = max(1, metrics["blocked"] + metrics["warn"] + metrics["shadow_blocked"])
    metrics["false_positive_proxy"] = min(
        1.0,
        round((retry_after_block + allowed_via_override) / fp_denominator, 3),
    )

    # Override survival summary (best-effort; full calc uses shared helper).
    metrics["override_survival_ratio"] = None  # computed separately if repo available
    return metrics


def _fmt_latency(value: int | None) -> str:
    return str(value) if value is not None else "n/a"


def _fmt_benchmark_markdown(metrics: dict, title: str = "reasoning-core benchmark") -> str:
    lines = [
        f"# {title}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total events | {metrics['total_events']} |",
        f"| Allowed | {metrics['allowed']} |",
        f"| Blocked | {metrics['blocked']} |",
        f"| Warn | {metrics['warn']} |",
        f"| Shadow blocked | {metrics['shadow_blocked']} |",
        f"| Fail-open | {metrics['fail_open']} |",
        f"| Allowed via override | {metrics['allowed_via_override']} |",
        f"| Operator overrides | {metrics['operator_override']} |",
        f"| Operator confirmed | {metrics['operator_confirmed']} |",
        f"| Scope-creep catches | {metrics['scope_creep_catches']} |",
        f"| Retry-after-block proxy | {metrics['retry_after_block']} |",
        f"| False-positive proxy | {metrics['false_positive_proxy']:.3f} |",
        f"| Median latency (ms) | {_fmt_latency(metrics['median_latency_ms'])} |",
        f"| p95 latency (ms) | {_fmt_latency(metrics['p95_latency_ms'])} |",
        f"| Median block latency (ms) | {_fmt_latency(metrics['median_block_latency_ms'])} |",
        f"| Token-cost proxy | {metrics['token_cost_proxy']} |",
        "",
        "## Events by class",
        "",
        "| Class | Count |",
        "|---|---|",
    ]
    for cls in sorted(metrics["severity"].keys(), key=lambda k: -metrics["severity"][k]):
        lines.append(f"| {cls} | {metrics['severity'][cls]} |")

    if metrics["signal_sources"]:
        lines.extend([
            "",
            "## Signal source decomposition",
            "",
            "| Source | Count |",
            "|---|---|",
        ])
        for src in sorted(metrics["signal_sources"].keys(), key=lambda k: -metrics["signal_sources"][k]):
            lines.append(f"| {src} | {metrics['signal_sources'][src]} |")

    if metrics.get("override_survival_ratio") is not None:
        lines.extend([
            "",
            "## Override survival",
            "",
            f"- Survival ratio: {metrics['override_survival_ratio']:.2%}",
        ])

    lines.extend([
        "",
        "_Notes: token-cost proxy is a synthetic unit for week-over-week comparison, not a dollar estimate. False-positive proxy is capped at 1.0 and counts retries-after-block plus allowed-via-override outcomes over blocking decisions; the same incident may appear in both counts._",
        "",
    ])
    return "\n".join(lines)


def _override_survival_ratio(audit_root: Path, days: int) -> float | None:
    """Compute a lightweight override survival ratio for the benchmark report."""
    repo_root = _git_repo_root()
    if repo_root is None:
        return None

    survived, reverted, _unknown = _override_survival_counts(audit_root, days, repo_root)
    total = survived + reverted
    if total == 0:
        return None
    return survived / total


def cmd_benchmark(args: argparse.Namespace) -> int:
    """One-command benchmark runner from the local audit log.

    Produces a Markdown report with blocked-edit counts by severity class,
    override survival, median latency, a token-cost proxy, and a false-positive
    proxy.  Supports --before / --after date windows for week-over-week
    comparison.
    """
    import datetime as _dt

    audit_root = Path(args.audit_root or _audit_root())
    before = args.before
    after = args.after
    days = args.days

    if before:
        try:
            _dt.datetime.strptime(before, "%Y-%m-%d")
        except ValueError:
            sys.stderr.write(f"--before must be YYYY-MM-DD, got {before}\n")
            return 1
    if after:
        try:
            after_dt = _dt.datetime.strptime(after, "%Y-%m-%d")
        except ValueError:
            sys.stderr.write(f"--after must be YYYY-MM-DD, got {after}\n")
            return 1
        # Ensure _walk_audit_events covers the requested window even when it is
        # older than the default --days value.
        days = max(days, (_dt.datetime.now() - after_dt).days + 1)

    # Warn when a --before-only window may be truncated by the default --days.
    if before and not after and days == args.days:
        sys.stderr.write(
            f"note: --before window is bounded by --days {days}; "
            "pass a larger --days or an explicit --after to widen the window\n"
        )

    metrics = _compute_benchmark(audit_root, days, before, after)
    # Override survival compares against the current working tree, so it is only
    # meaningful for the trailing --days window. Skip it for historical windows.
    if before or after:
        metrics["override_survival_ratio"] = None
    else:
        metrics["override_survival_ratio"] = _override_survival_ratio(audit_root, days)

    if args.json:
        out_path = Path(args.json)
        try:
            out_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"failed to write JSON: {exc}\n")
            return 1

    report = _fmt_benchmark_markdown(metrics, title="reasoning-core benchmark")
    if args.output:
        try:
            Path(args.output).write_text(report + "\n", encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"failed to write report: {exc}\n")
            return 1
    else:
        sys.stdout.write(report + "\n")
    return 0


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(prog="rc", description="reasoning-core operator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    e = sub.add_parser("explain")
    e.add_argument("decision_id")
    e.set_defaults(func=cmd_explain)
    sub.add_parser("bypass-next").set_defaults(func=cmd_bypass_next)
    sub.add_parser("confirm-next").set_defaults(func=cmd_confirm_next)
    ee = sub.add_parser(
        "enable-enforcement",
        help="scaffold PLAN.md and flip repo to copilot mode",
    )
    ee.set_defaults(func=cmd_enable_enforcement)
    s = sub.add_parser("skip-file")
    s.add_argument("path")
    s.set_defaults(func=cmd_skip_file)
    u = sub.add_parser("unskip-file")
    u.add_argument("path")
    u.set_defaults(func=cmd_unskip_file)
    sp_cmd = sub.add_parser(
        "score-pr",
        help="score changed files between two git refs (CI / local dry-run)",
    )
    sp_cmd.add_argument("--base-ref", required=True, help="base git ref")
    sp_cmd.add_argument("--head-ref", default="HEAD", help="head git ref")
    sp_cmd.add_argument("--mode", default="symbolic", choices=["sidecar", "symbolic"], help="scoring backend")
    sp_cmd.add_argument("--include", default=None, help="comma-separated extension globs (default: common source files)")
    sp_cmd.add_argument("--output", default=None, help="Markdown report path")
    sp_cmd.add_argument("--json", default=None, help="optional JSON output path")
    sp_cmd.add_argument("--fail-on-block", action="store_true", help="exit non-zero if any file is blocked")
    sp_cmd.set_defaults(func=cmd_score_pr)
    re_cmd = sub.add_parser(
        "reasoning-efficiency",
        help="audit-log composite metric (audit 2026-06-01 §7)",
    )
    re_cmd.add_argument("--days", type=int, default=7)
    re_cmd.add_argument("--audit-root", default=None)
    re_cmd.set_defaults(func=cmd_reasoning_efficiency)
    os_cmd = sub.add_parser(
        "override-survival",
        help="override survival ratio from audit log (2026-06-15)",
    )
    os_cmd.add_argument("--days", type=int, default=30)
    os_cmd.add_argument("--audit-root", default=None)
    os_cmd.set_defaults(func=cmd_override_survival)
    ah_cmd = sub.add_parser(
        "audit-history",
        help="mine recent git history and label commits for calibration feedback",
    )
    ah_cmd.add_argument("--project-dir", default=None)
    ah_cmd.add_argument("-n", type=int, default=50)
    ah_cmd.add_argument("--json", action="store_true")
    ah_cmd.add_argument("--reasons", action="store_true")
    ah_cmd.set_defaults(func=cmd_audit_history)
    bench_cmd = sub.add_parser(
        "benchmark",
        help="one-command benchmark report from the local audit log",
    )
    bench_cmd.add_argument("--days", type=int, default=30)
    bench_cmd.add_argument("--audit-root", default=None)
    bench_cmd.add_argument("--before", default=None, help="include events strictly before this date (YYYY-MM-DD)")
    bench_cmd.add_argument("--after", default=None, help="include events on or after this date (YYYY-MM-DD)")
    bench_cmd.add_argument("--output", default=None, help="Markdown report output path")
    bench_cmd.add_argument("--json", default=None, help="optional JSON metrics output path")
    bench_cmd.set_defaults(func=cmd_benchmark)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
