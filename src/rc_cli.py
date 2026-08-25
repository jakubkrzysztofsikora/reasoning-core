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
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Make `src` importable when run as a script (`python src/rc_cli.py`): the repo
# root -- not just src/ -- must be on sys.path before importing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src import baselines  # noqa: E402

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
    "RC_BYPASS_NEXT", "RC_PROJECT_INDEX", "RC_ENFORCEMENT_AUTH",
)


def _audit_root() -> Path:
    return Path(os.environ.get(
        "RC_AUDIT_ROOT",
        os.path.expanduser("~/.local/share/reasoning-core/events"),
    ))


def _today_dir() -> Path:
    import datetime as dt
    return _audit_root() / dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def cmd_baseline_capture(args: argparse.Namespace) -> int:
    """Capture a registry manifest; refuse to overwrite an existing baseline."""
    target = baselines.manifest_path(args.id)
    if target.exists():
        sys.stderr.write(f"baseline already exists (immutable): {target}\n")
        return 2
    local_root = Path(args.artifact_root or os.path.expanduser("~/.local/share/reasoning-core/baselines")) / args.id
    local_root.mkdir(parents=True, exist_ok=True)
    manifest = baselines.create_manifest(args.id, audit_root=_audit_root(), artifacts=[local_root])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(target)
    return 0


def cmd_baseline_list(_args: argparse.Namespace) -> int:
    if not baselines.REGISTRY_ROOT.is_dir():
        return 0
    for path in sorted(baselines.REGISTRY_ROOT.glob("*.json")):
        try:
            manifest = baselines.load_manifest(path.stem)
            print(f"{manifest['baseline_id']}\t{manifest['captured_at']}\t{manifest.get('code', {}).get('git_sha', 'unknown')}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"invalid baseline {path}: {exc}\n")
            return 2
    return 0


def cmd_baseline_show(args: argparse.Namespace) -> int:
    manifest = baselines.load_manifest(args.id)
    if args.verify:
        manifest = {**manifest, "artifact_verification": baselines.verify_artifacts(manifest)}
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def cmd_baseline_compare(args: argparse.Namespace) -> int:
    left = baselines.load_manifest(args.from_id)
    right = baselines.load_manifest(args.to_id)
    print(json.dumps(baselines.compare_manifests(left, right), indent=2, sort_keys=True))
    return 0


def _guard_hash_store_path() -> Path:
    return Path(os.environ.get("RC_STATE_DIR", os.path.expanduser("~/.local/state/reasoning-core"))) / "guard_hashes.json"


def _init_guard_hashes(file_paths: list[str], store_path: str | None = None) -> tuple[int, list[str]]:
    """Initialize the guard-hash store with the current hashes of the given files.

    This is the only path that can add entries to the store. Called by the
    operator explicitly via `rc guard-hash --init`.

    Returns (status, warnings):
      status: 0 on success, 1 on partial success, 2 on failure
      warnings: list of human-readable warnings about recovered/skipped entries
    """
    store = Path(store_path) if store_path else _guard_hash_store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    records: dict[str, str] = {}
    warnings: list[str] = []

    if store.is_file():
        try:
            existing = json.loads(store.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                records = existing
            else:
                warnings.append(
                    f"existing store at {store} had unexpected type "
                    f"{type(existing).__name__}; backing up and starting fresh"
                )
                store.rename(store.with_suffix(".jsonl.corrupt"))
        except json.JSONDecodeError as exc:
            warnings.append(
                f"existing store at {store} was corrupt ({exc}); "
                f"backing up to {store.with_suffix('.jsonl.corrupt')} and starting fresh"
            )
            store.rename(store.with_suffix(".jsonl.corrupt"))
        except OSError as exc:
            warnings.append(f"could not read existing store at {store}: {exc}")

    added = 0
    skipped = 0
    for fp in file_paths:
        path = Path(fp).resolve()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, PermissionError) as exc:
            warnings.append(f"could not hash {path}: {exc}")
            skipped += 1
            continue
        records[str(path)] = digest
        added += 1

    if added == 0 and not records:
        sys.stderr.write(
            f"no guard files registered; store not written to {store}\n"
        )
        for w in warnings:
            sys.stderr.write(f"  warning: {w}\n")
        return (2, warnings)

    store.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return (0, warnings)


def _verify_guard_hash(file_path: str, store_path: str | None = None) -> tuple[bool, str]:
    """Verify a guard file against a stored SHA-256 hash.

    Returns (ok, reason):
      - (True, "match") when verified
      - (False, "missing_file") when guard file absent
      - (False, "store_missing") when no store file
      - (False, "store_corrupt") when store unreadable or not a dict
      - (False, "not_registered") when file is not in store
      - (False, "mismatch") when hash differs

    Use `rc guard-hash --init` (operator-authenticated) to register hashes.
    """
    from rc_verify_guard import _verify as _verify_standalone
    return _verify_standalone(file_path, Path(store_path) if store_path else None)


def cmd_guard_hash(args: argparse.Namespace) -> int:
    """Verify guard files and the enforcement config against stored hashes.

    Includes `rc_cli.py` itself (no self-exclusion) to prevent the
    self-referential backdoor where an attacker who edits `rc_cli.py`
    can rewrite `_operator_authenticated` to always return True.

    The actual hash comparison is delegated to `rc_verify_guard.py`,
    which has no dependency on this CLI module — so tampering with
    `rc_cli.py` cannot bypass the verify path.
    """
    project_dir = _project_dir()
    repo_root = Path(__file__).resolve().parent.parent
    guard_files = [
        repo_root / "src" / "hooks" / "pre_edit_guard.py",
        repo_root / "src" / "hooks" / "_dispatch.py",
        repo_root / "src" / "hooks" / "_guard_paths.py",
        repo_root / "src" / "hooks" / "_kill_switches.py",
        repo_root / "src" / "hooks" / "_magic_comments.py",
        repo_root / "src" / "hooks" / "_rule_engine.py",
        repo_root / "src" / "hooks" / "audit_log.py",
        repo_root / "src" / "rc_cli.py",
    ]
    envrc_local = project_dir / ".envrc.local"
    plan_md = project_dir / "PLAN.md"
    all_files = [str(g) for g in guard_files] + [str(envrc_local), str(plan_md)]

    if getattr(args, "init", False):
        if not _operator_authenticated():
            sys.stderr.write("rc guard-hash --init: operator authentication required.\n")
            return 1
        existing = []
        for f in all_files:
            if Path(f).is_file():
                existing.append(f)
        status, warnings = _init_guard_hashes(existing)
        for w in warnings:
            sys.stderr.write(f"  warning: {w}\n")
        sys.stdout.write(f"registered hashes for {len(existing)} guard files\n")
        return status

    store = _guard_hash_store_path()
    if not store.is_file():
        sys.stderr.write(
            f"guard-hash store not initialized at {store}.\n"
            "Run `rc guard-hash --init` (operator-authenticated) to register hashes.\n"
        )
        return 2

    # Delegate the actual comparison to rc_verify_guard.py to avoid the
    # self-referential backdoor where tampering with rc_cli.py could
    # bypass _verify_guard_hash logic in this file.
    verify_script = repo_root / "src" / "rc_verify_guard.py"
    if not verify_script.is_file():
        sys.stderr.write(f"verifier missing at {verify_script}\n")
        return 2
    proc = subprocess.run(
        [sys.executable, str(verify_script), "--store", str(store)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    # Also check the envrc.local and PLAN.md (these are project-specific
    # and not in the verifier's hardcoded list)
    all_ok = proc.returncode == 0
    sys.stdout.write("== reasoning-core guard-hash ==\n")
    for extra in (envrc_local, plan_md):
        if extra.is_file():
            ok, reason = _verify_guard_hash_standalone(str(extra), str(store))
            status_label = "ok" if ok else f"TAMPERED ({reason})"
            sys.stdout.write(f"  {status_label:<25} {extra}\n")
            if not ok:
                all_ok = False

    if all_ok:
        sys.stdout.write("\nall guard files match stored hashes\n")
        return 0
    sys.stderr.write("\nTAMPERING DETECTED: one or more guard files/config changed.\n")
    try:
        audit_log.record_block(None)
    except OSError as exc:
        sys.stderr.write(
            f"  CRITICAL: tamper detected but audit log write failed: {exc}\n"
        )
    except Exception as exc:
        sys.stderr.write(
            f"  CRITICAL: tamper detected but audit log write raised "
            f"{type(exc).__name__}: {exc}\n"
        )
    return 2


def _verify_guard_hash_standalone(file_path: str, store_path: str) -> tuple[bool, str]:
    """Inline minimal verifier for project-local files (envrc, PLAN.md).

    Returns (ok, reason). Used by cmd_guard_hash for files outside the
    hardcoded verifier list.
    """
    from rc_verify_guard import _verify
    return _verify(file_path, Path(store_path))


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


_SENTINEL_START = "# >>> rc enforcement >>>"
_SENTINEL_END = "# <<< rc enforcement <<<"
_SENTINEL_RE = re.compile(
    rf"\n?{re.escape(_SENTINEL_START)}.*?{re.escape(_SENTINEL_END)}\n?",
    re.DOTALL,
)


def _enforcement_block(hard: bool = False) -> str:
    """Build the fenced enforcement block for the requested stage.

    Stage 1 (hard=False): copilot mode with warn-only plan-grounding.
    Stage 2 (hard=True): copilot mode with hard plan-grounding block.
    """
    plan_grounding = "2" if hard else "1"
    stage_label = "Stage 2 (hard plan-grounding)" if hard else "Stage 1 (warn-only plan-grounding)"
    block_name = "stage2" if hard else "stage1"
    return (
        f"{_SENTINEL_START}\n"
        f"# Enabled via `rc enable-enforcement{' --hard' if hard else ''}`. "
        f"{stage_label}. Revert with `rc disable-enforcement`.\n"
        f"export RC_MODE=copilot\n"
        f"export RC_SHADOW_MODE=0\n"
        f"export RC_PLAN_BLOCK=1\n"
        f"export RC_PLAN_GROUNDING={plan_grounding}\n"
        f"export RC_ORACLE_BLOCK=1\n"
        f"export RC_RULE_ENGINE=1\n"
        f"export RC_PROJECT_INDEX=1\n"
        f"export S2_FAIL_CLOSED=1\n"
        f"{_SENTINEL_END}\n"
    )


_ENFORCEMENT_BLOCK_STAGE1 = _enforcement_block(hard=False)
_ENFORCEMENT_BLOCK_STAGE2 = _enforcement_block(hard=True)


_AUTH_MIN_TOKEN_LEN = 16
_AUTH_TOKEN_FILE = Path(os.environ.get(
    "RC_AUTH_TOKEN_FILE",
    os.path.expanduser("~/.local/state/reasoning-core/auth_token"),
))


def _read_auth_token_from_file() -> str | None:
    """Read stored auth token from the platform-appropriate secret store.

    Order:
      1. macOS keychain via `security find-generic-password`.
      2. Token file at `RC_AUTH_TOKEN_FILE` (Linux/CI fallback).

    Returns the stored token, or None if no backend is available or read failed.
    """
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "reasoning-core-enforcement", "-a", os.environ.get("USER", ""), "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except FileNotFoundError:
        pass  # macOS-only binary absent on Linux/Windows
    except Exception:
        pass

    if _AUTH_TOKEN_FILE.is_file():
        try:
            return _AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return None


def _operator_authenticated() -> bool:
    """Return True if the operator has authenticated for enforcement changes.

    Authentication requires one of:
      1. `RC_ENFORCEMENT_TOKEN` env var matches the stored token
         (compared in constant time). This is the cross-platform escape hatch
         for headless / CI usage. The env var must be ≥16 chars.
      2. macOS keychain contains a stored reasoning-core enforcement token.
      3. Linux/Windows fallback: token file at `RC_AUTH_TOKEN_FILE` (default
         `~/.local/state/reasoning-core/auth_token`).

    TTY presence is NOT sufficient — the agent can allocate a pty.
    Just having `RC_ENFORCEMENT_TOKEN` set to any non-empty string is NOT
    sufficient — an agent can set env vars in its own shell. The token must
    match the stored secret.

    Run `rc auth-bootstrap` to seed the token file on Linux/Windows.
    """
    token = os.environ.get("RC_ENFORCEMENT_TOKEN", "")
    if len(token) >= _AUTH_MIN_TOKEN_LEN:
        stored = _read_auth_token_from_file()
        if stored is not None and _constant_time_eq(token, stored):
            return True
    return False


def cmd_auth_bootstrap(args: argparse.Namespace) -> int:
    """Generate and store a fresh enforcement token.

    Writes the token to the platform-appropriate secret store and prints
    it once on stdout. The operator must copy it into `RC_ENFORCEMENT_TOKEN`
    or use it in a CI secret.

    macOS: stores in keychain via `security add-generic-password`.
    Linux/Windows: writes to `RC_AUTH_TOKEN_FILE` with 0600 permissions.
    """
    import secrets
    token = secrets.token_urlsafe(32)

    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "add-generic-password",
                 "-s", "reasoning-core-enforcement",
                 "-a", os.environ.get("USER", ""),
                 "-w", token,
                 "-U"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                sys.stdout.write(f"token stored in keychain for user {os.environ.get('USER','')}\n")
                sys.stdout.write(f"token (copy now, won't be shown again): {token}\n")
                return 0
            sys.stderr.write(f"keychain add failed: {r.stderr}\n")
        except Exception as exc:
            sys.stderr.write(f"keychain add failed: {exc}\n")

    # Linux/Windows fallback
    try:
        _AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(_AUTH_TOKEN_FILE, token + "\n")
        os.chmod(_AUTH_TOKEN_FILE, 0o600)
        sys.stdout.write(f"token stored in {_AUTH_TOKEN_FILE} (mode 0600)\n")
        sys.stdout.write(f"token (copy now, won't be shown again): {token}\n")
        return 0
    except OSError as exc:
        sys.stderr.write(f"failed to write token file: {exc}\n")
        return 1


def _constant_time_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to path atomically: write to temp file, fsync, rename."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_unlink(path: Path) -> bool:
    """Unlink path, refusing to follow symlinks. Returns True if removed."""
    if not path.exists():
        return False
    if path.is_symlink():
        # Don't follow symlinks — could be a symlink to a critical file
        path.unlink()
        return True
    if path.is_file():
        path.unlink()
        return True
    return False


def _update_envrc_local(project_dir: Path, hard: bool = False) -> Path:
    """Idempotently write the reasoning-core enforcement block with fenced markers."""
    envrc_local = project_dir / ".envrc.local"
    block = _ENFORCEMENT_BLOCK_STAGE2 if hard else _ENFORCEMENT_BLOCK_STAGE1
    existing = ""
    if envrc_local.is_file() and not envrc_local.is_symlink():
        existing = envrc_local.read_text(encoding="utf-8")
        existing = _SENTINEL_RE.sub("\n", existing).strip("\n")
        if existing:
            existing += "\n"
    _atomic_write_text(envrc_local, existing + block)

    # Post-condition verification: sentinel must be present in written file
    written = envrc_local.read_text(encoding="utf-8")
    if _SENTINEL_START not in written or _SENTINEL_END not in written:
        raise RuntimeError(
            f"post-condition failed: sentinel markers not found in {envrc_local} after write"
        )
    return envrc_local


def _remove_envrc_local_block(project_dir: Path) -> tuple[Path, bool]:
    """Remove only the fenced reasoning-core enforcement block.

    Returns (path, removed) where removed indicates whether a sentinel block
    was actually present and removed.
    """
    envrc_local = project_dir / ".envrc.local"
    if not envrc_local.is_file() or envrc_local.is_symlink():
        return (envrc_local, False)
    existing = envrc_local.read_text(encoding="utf-8")
    had_block = _SENTINEL_START in existing
    cleaned = _SENTINEL_RE.sub("\n", existing).strip("\n")
    if cleaned:
        _atomic_write_text(envrc_local, cleaned + "\n")
    else:
        _atomic_unlink(envrc_local)
    return (envrc_local, had_block)


def cmd_enable_enforcement(args: argparse.Namespace) -> int:
    """First-run wizard: flip repo to staged copilot mode after operator auth."""
    project_dir = _project_dir()
    if not project_dir.is_dir():
        sys.stderr.write(f"project directory does not exist: {project_dir}\n")
        return 1

    if not _operator_authenticated():
        sys.stderr.write(
            "rc enable-enforcement: operator authentication required.\n"
            "Run from an interactive shell, set RC_ENFORCEMENT_AUTH, or store a\n"
            "token in the macOS keychain with service 'reasoning-core-enforcement'.\n"
        )
        return 1

    plan_path = project_dir / "PLAN.md"
    if not plan_path.is_file():
        sys.stderr.write(
            f"rc enable-enforcement: {plan_path} does not exist.\n"
            "Write a plan manually or run `rc init-plan` before enabling enforcement.\n"
        )
        return 1

    envrc_local = _update_envrc_local(project_dir, hard=args.hard)

    sys.stdout.write("== reasoning-core enforcement enabled ==\n\n")
    sys.stdout.write(f"plan: {plan_path}\n")
    sys.stdout.write(f"wrote enforcement config: {envrc_local}\n")
    stage = "Stage 2 (hard plan-grounding)" if args.hard else "Stage 1 (warn-only plan-grounding)"
    sys.stdout.write(f"stage: {stage}\n\n")
    sys.stdout.write("next:\n")
    sys.stdout.write("  direnv reload      # or: source .envrc.local\n")
    sys.stdout.write("  rc status          # confirm RC_MODE=copilot\n")
    return 0


def cmd_disable_enforcement(_args: argparse.Namespace) -> int:
    """Revert repo to advisory mode by removing the enforcement block."""
    project_dir = _project_dir()
    if not project_dir.is_dir():
        sys.stderr.write(f"project directory does not exist: {project_dir}\n")
        return 1

    if not _operator_authenticated():
        sys.stderr.write(
            "rc disable-enforcement: operator authentication required.\n"
        )
        return 1

    envrc_local, removed = _remove_envrc_local_block(project_dir)
    if removed:
        sys.stdout.write(f"removed enforcement block from {envrc_local}\n")
    elif envrc_local.exists():
        sys.stdout.write(f"no enforcement block found in {envrc_local}\n")
    else:
        sys.stdout.write("no enforcement block found; nothing to remove\n")
    sys.stdout.write("next: direnv reload  # or: source .envrc.local\n")
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
    earliest = _dt.datetime.now(tz=_dt.timezone.utc).date() - _dt.timedelta(days=days - 1)
    if not audit_root.is_dir():
        return
    for day_dir in sorted(audit_root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]")):
        try:
            day = _dt.datetime.strptime(day_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < earliest:
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
        ev_root = ev.get("project_dir")
        event_repo = Path(ev_root) if ev_root else repo_root
        if event_repo is None or not (event_repo / ".git").exists():
            event_repo = repo_root
        if event_repo is None:
            unknown += 1
            continue
        try:
            rel_fp = str(Path(fp).resolve().relative_to(Path(event_repo).resolve()))
        except ValueError:
            unknown += 1
            continue
        try:
            r = subprocess.run(
                ["git", "show", f"{git_head}:{rel_fp}"],
                capture_output=True, text=True, timeout=5, cwd=str(event_repo),
            )
            content_at_override = r.stdout if r.returncode == 0 else None
        except Exception:
            content_at_override = None
        fpath = Path(event_repo) / rel_fp
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



def _reconcile_missing_gate_events(project_dir: str, audit_root: str, session_id: str) -> list[str]:
    """Diff git working tree against gate_edit audit rows for the session.

    Returns a list of file paths (relative to repo root) that were modified
    on disk but lack a corresponding gate_edit audit row in the current
    session. Scans yesterday + today to handle sessions that span midnight.
    Includes `.jsonl.gz` rotated logs.
    """
    import datetime as _dt
    import subprocess

    project = Path(project_dir)
    if not (project / ".git").exists():
        return []

    # Files changed on disk (relative to repo root).
    r = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, cwd=str(project), timeout=30,
    )
    changed: set[str] = set()
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            line = line.rstrip("\r")
            if line.startswith("?? "):
                changed.add(line[3:].strip())
            elif len(line) >= 4 and line[2] == " ":
                changed.add(line[3:].strip())
            elif len(line) >= 3 and line[1] in ("M", "A", "D", "R", "C", "U") and line[2] == " ":
                changed.add(line[2:].strip().split(" -> ")[-1])

    # Gated files from audit log: normalise absolute paths to repo-relative
    # so they can be compared with `git status` output.
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=str(project), timeout=5,
    )
    repo_root_path = Path(repo_root.stdout.strip()) if repo_root.returncode == 0 else project

    gated: set[str] = set()
    root = Path(audit_root)
    today = _dt.datetime.now(_dt.timezone.utc).date()
    # Scan yesterday and today to handle sessions that span midnight.
    scan_days = [today - _dt.timedelta(days=1), today]
    for day in scan_days:
        day_str = day.strftime("%Y-%m-%d")
        day_dir = root / day_str
        if not day_dir.is_dir():
            continue
        log_paths = list(day_dir.glob("*.jsonl")) + list(day_dir.glob("*.jsonl.gz"))
        for log_path in log_paths:
            try:
                if log_path.suffix == ".gz":
                    import gzip
                    fh = gzip.open(log_path, "rt", encoding="utf-8")
                else:
                    fh = log_path.open("r", encoding="utf-8")
                with fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        if ev.get("session_id") == session_id and ev.get("decision") in (
                            "allowed", "blocked", "warn", "shadow_blocked", "allowed_via_override",
                        ):
                            fp = ev.get("file_path")
                            if isinstance(fp, str) and fp:
                                fp_path = Path(fp)
                                # Normalise to repo-relative. If fp is
                                # absolute but not under repo_root, try
                                # resolving via the symlink-resolved path
                                # or fall back to the literal fp with a
                                # warning.
                                if fp_path.is_absolute():
                                    try:
                                        fp = str(fp_path.relative_to(repo_root_path))
                                    except ValueError:
                                        # Try resolving symlinks in repo_root
                                        try:
                                            fp = str(
                                                fp_path.resolve().relative_to(
                                                    repo_root_path.resolve()
                                                )
                                            )
                                        except ValueError:
                                            # Fall back: compare on basename
                                            # so we don't miss-file the entry.
                                            gated.add(fp_path.name)
                                            continue
                                gated.add(fp)
            except OSError:
                continue

    return sorted(changed - gated)


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Post-session safety net: flag files written without a gate_edit call."""
    project_dir = _project_dir()
    if not project_dir.is_dir():
        sys.stderr.write(f"project directory does not exist: {project_dir}\n")
        return 1

    sid = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("RC_SESSION_ID") or "default"
    missing = _reconcile_missing_gate_events(str(project_dir), str(_audit_root()), sid)

    if getattr(args, "json", False):
        sys.stdout.write(json.dumps({"missing": missing, "session_id": sid}) + "\n")
        return 0 if not missing else 1

    if not missing:
        sys.stdout.write("rc reconcile: all changed files have a gate_edit audit row.\n")
        return 0
    sys.stdout.write("rc reconcile: files changed without gate_edit audit row:\n")
    for fp in missing:
        sys.stdout.write(f"  {fp}\n")
    return 1


def cmd_label(args: argparse.Namespace) -> int:
    """Label an audit decision for the training set.

    Without --random, takes a decision_id argument. With --random, picks
    one unlabeled decision from recent audit (last 7 days by default).

    Interactive flow (no --yes): shows file, before/after snippet, and asks
    for the 5 labels (y/n each).

    Non-interactive (--yes): reads labels from --labels flag as
    "scope_drift=yes,plan_violation=no,..." or from a JSON file with --from-file.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    try:
        import _training_set as ts
    except ImportError as exc:
        sys.stderr.write(f"could not load training_set module: {exc}\n")
        return 1

    if args.random:
        candidate = ts.pick_random_unlabeled(days=args.days)
        if not candidate:
            sys.stderr.write(f"no unlabeled decisions found in last {args.days} days\n")
            return 1
        decision_id = candidate["decision_id"]
        sys.stdout.write(f"picked random unlabeled decision: {decision_id}\n")
        sys.stdout.write(f"  file:     {candidate.get('file_path', '?')}\n")
        sys.stdout.write(f"  decision: {candidate.get('decision', '?')}\n")
        sys.stdout.write(f"  source:   {candidate.get('signal_source', '?')}\n\n")
    else:
        decision_id = args.decision_id
        if not decision_id:
            sys.stderr.write("usage: rc label <decision-id> [--random]\n")
            return 1

    if ts.already_labeled(decision_id):
        sys.stderr.write(f"decision {decision_id} is already labeled\n")
        return 1

    audit_row = ts._lookup_audit_row(decision_id, days=max(args.days, 7))
    if not audit_row:
        sys.stderr.write(f"warning: decision_id {decision_id} not found in audit log\n")

    file_path = audit_row.get("file_path", "?")
    sys.stdout.write("=" * 60 + "\n")
    sys.stdout.write(f"decision_id: {decision_id}\n")
    sys.stdout.write(f"file:        {file_path}\n")
    sys.stdout.write(f"decision:    {audit_row.get('decision', '?')}\n")
    sys.stdout.write(f"signal:      {audit_row.get('signal_source', '?')}\n")
    sys.stdout.write("=" * 60 + "\n")
    before_src = (audit_row.get("before_src") or "")[:500]
    after_src = (audit_row.get("after_src") or "")[:500]
    if before_src or after_src:
        sys.stdout.write("--- before ---\n")
        sys.stdout.write(before_src + "\n")
        sys.stdout.write("--- after ---\n")
        sys.stdout.write(after_src + "\n")
        sys.stdout.write("=" * 60 + "\n")

    labels: dict[str, bool] = {}
    if args.from_file:
        try:
            data = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
            labels = {k: bool(v) for k, v in data.items() if k in ts._LABELS}
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"could not read --from-file: {exc}\n")
            return 1
    elif args.labels:
        for pair in args.labels.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            k = k.strip()
            if k in ts._LABELS:
                labels[k] = v.strip().lower() in ("yes", "true", "1", "y")
    elif args.yes:
        sys.stderr.write("--yes requires --labels or --from-file\n")
        return 1
    else:
        for label in ts._LABELS:
            sys.stdout.write(f"  {label}? [y/n] ")
            sys.stdout.flush()
            try:
                ans = input().strip().lower()
            except EOFError:
                sys.stderr.write("\nno TTY; pass --labels or --from-file for non-interactive\n")
                return 1
            labels[label] = ans in ("y", "yes", "true", "1")

    notes = ""
    if not args.yes:
        sys.stdout.write("  notes (optional): ")
        sys.stdout.flush()
        try:
            notes = input().strip()
        except EOFError:
            notes = ""

    label = ts.label_decision_id(
        decision_id,
        labels,
        labeler_id=os.environ.get("USER", "operator"),
        notes=notes,
    )
    sys.stdout.write(f"\nstored label for {decision_id}\n")
    sys.stdout.write(f"  rationale_quality_failure = {label.rationale_quality_failure}\n")
    return 0


def cmd_label_stats(_args: argparse.Namespace) -> int:
    """Show progress toward the per-label training target."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    try:
        import _training_set as ts
    except ImportError as exc:
        sys.stderr.write(f"could not load training_set module: {exc}\n")
        return 1

    p = ts.progress()
    sys.stdout.write("== training-set progress ==\n")
    sys.stdout.write(f"target per label: {p['target_per_label']}\n")
    sys.stdout.write(f"total stored:     {p['total_stored']}\n\n")
    sys.stdout.write(f"{'label':<25} {'count':>5} {'remaining':>10}\n")
    for label in ts._LABELS:
        c = p["counts"].get(label, 0)
        r = p["remaining"][label]
        marker = "OK" if r == 0 else "..."
        sys.stdout.write(f"  {label:<23} {c:>5} {r:>10} {marker}\n")
    if all(p["remaining"][k] == 0 for k in ts._LABELS):
        sys.stdout.write("\nALL LABELS REACHED TARGET — ready for n=100 eval\n")
        return 0
    sys.stdout.write(f"\nstore: {ts.store_path()}\n")
    return 1


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
    scope_creep_blocked = 0
    scope_creep_warned = 0
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
            if decision in ("blocked", "shadow_blocked"):
                scope_creep_blocked += 1
            else:
                scope_creep_warned += 1

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
        "scope_creep_blocked": scope_creep_blocked,
        "scope_creep_warned": scope_creep_warned,
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
    fp_denominator = metrics["blocked"] + metrics["shadow_blocked"]
    if fp_denominator == 0:
        metrics["false_positive_proxy"] = None
    else:
        metrics["false_positive_proxy"] = min(
            1.0,
            round((retry_after_block + allowed_via_override) / fp_denominator, 3),
        )

    # Override survival summary (best-effort; full calc uses shared helper).
    metrics["override_survival_ratio"] = None  # computed separately if repo available
    return metrics


def _fmt_latency(value: int | None) -> str:
    return str(value) if value is not None else "n/a"


def _fmt_ratio(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


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
        f"| Scope-creep catches (total) | {metrics['scope_creep_catches']} |",
        f"| Scope-creep prevented (blocked) | {metrics['scope_creep_blocked']} |",
        f"| Scope-creep advised only (warn) | {metrics['scope_creep_warned']} |",
        f"| Retry-after-block proxy | {metrics['retry_after_block']} |",
        f"| False-positive proxy | {_fmt_ratio(metrics['false_positive_proxy'])} |",
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
            _atomic_write_text(out_path, json.dumps(metrics, indent=2) + "\n")
        except OSError as exc:
            sys.stderr.write(f"failed to write JSON: {exc}\n")
            return 1

    report = _fmt_benchmark_markdown(metrics, title="reasoning-core benchmark")
    if args.output:
        try:
            _atomic_write_text(Path(args.output), report + "\n")
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
    baseline = sub.add_parser("baseline", help="capture and inspect immutable evaluation baselines")
    baseline_sub = baseline.add_subparsers(dest="baseline_cmd", required=True)
    baseline_capture = baseline_sub.add_parser("capture", help="capture a new immutable baseline manifest")
    baseline_capture.add_argument("--id", default=baselines.DEFAULT_ID)
    baseline_capture.add_argument("--artifact-root", default=None)
    baseline_capture.set_defaults(func=cmd_baseline_capture)
    baseline_sub.add_parser("list", help="list committed baseline manifests").set_defaults(func=cmd_baseline_list)
    baseline_show = baseline_sub.add_parser("show", help="print one baseline manifest")
    baseline_show.add_argument("id")
    baseline_show.add_argument("--verify", action="store_true", help="verify hashes of available local artifacts")
    baseline_show.set_defaults(func=cmd_baseline_show)
    baseline_compare = baseline_sub.add_parser("compare", help="compare two immutable manifests")
    baseline_compare.add_argument("from_id")
    baseline_compare.add_argument("to_id")
    baseline_compare.set_defaults(func=cmd_baseline_compare)
    gh = sub.add_parser(
        "guard-hash",
        help="verify guard files and enforcement config against stored hashes",
    )
    gh.add_argument("--init", action="store_true", help="register current hashes (operator-authenticated)")
    gh.set_defaults(func=cmd_guard_hash)
    e = sub.add_parser("explain")
    e.add_argument("decision_id")
    e.set_defaults(func=cmd_explain)
    sub.add_parser("bypass-next").set_defaults(func=cmd_bypass_next)
    sub.add_parser("confirm-next").set_defaults(func=cmd_confirm_next)
    ee = sub.add_parser(
        "enable-enforcement",
        help="flip repo to copilot mode after operator authentication (requires PLAN.md)",
    )
    ee.add_argument("--hard", action="store_true", help="enable Stage 2 hard plan-grounding")
    ee.set_defaults(func=cmd_enable_enforcement)
    sub.add_parser(
        "disable-enforcement",
        help="revert repo to advisory mode by removing the enforcement block",
    ).set_defaults(func=cmd_disable_enforcement)
    sub.add_parser(
        "auth-bootstrap",
        help="generate and store a fresh enforcement token (macOS keychain or token file)",
    ).set_defaults(func=cmd_auth_bootstrap)
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
    r = sub.add_parser(
        "reconcile",
        help="diff git working tree against gate_edit audit rows for this session",
    )
    r.add_argument("--json", action="store_true", help="emit JSON output for hook parsing")
    r.set_defaults(func=cmd_reconcile)
    lab = sub.add_parser(
        "label",
        help="label an audit decision for the SWE-bench eval training set",
    )
    lab.add_argument("decision_id", nargs="?", default=None,
                     help="audit decision_id to label (omit if --random)")
    lab.add_argument("--random", action="store_true",
                    help="pick a random unlabeled decision from recent audit")
    lab.add_argument("--days", type=int, default=7,
                    help="how far back to look for --random (default 7)")
    lab.add_argument("--labels", default=None,
                    help='non-interactive labels, e.g. "scope_drift=yes,plan_violation=no"')
    lab.add_argument("--from-file", default=None,
                    help="read labels from a JSON file")
    lab.add_argument("--yes", action="store_true",
                    help="non-interactive (requires --labels or --from-file)")
    lab.set_defaults(func=cmd_label)
    sub.add_parser(
        "label-stats",
        help="show progress toward per-label training target",
    ).set_defaults(func=cmd_label_stats)
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
