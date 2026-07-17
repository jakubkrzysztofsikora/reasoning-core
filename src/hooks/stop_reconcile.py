#!/usr/bin/env python3
"""Stop hook: run rc reconcile to catch MCP-skip scenarios.

Fires at the end of a session (Stop event in Claude Code, or equivalent in
other hosts). Calls `rc reconcile` to diff git working tree against gate_edit
audit rows. If files were written without a gate call, emits a structured
JSON decision to stdout and an additionalContext blurb.

Protocol (Claude Code Stop hook):
  - stdin: JSON payload with `session_id`, `transcript_path`, `cwd`,
    `hook_event_name`, `stop_hook_active`.
  - stdout: JSON {"decision": "approve"|"block", "reason": "..."} when MCP-skip
    detected, else exit 0 silently.
  - stderr: human-readable summary.
  - exit code 0 on approve, 2 on block (stderr fed back to Claude).
  - `stop_hook_active: true` means this is a retry — honor the request and
    approve to avoid infinite loop.

In advisory mode (RC_MODE=advise): exit 0, audit-only, log to stderr.
In copilot mode (RC_MODE=copilot): exit 2 with JSON block decision when
MCP-skip detected, forcing the agent to call gate_edit before session ends.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _HOOKS_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _project_dir(payload: dict) -> str:
    """Resolve project dir from payload `cwd` (preferred), then env, then cwd."""
    payload_cwd = payload.get("cwd")
    if isinstance(payload_cwd, str) and payload_cwd:
        return str(Path(payload_cwd).resolve())
    return str(
        Path(
            os.environ.get("RC_RUN_DIR")
            or os.environ.get("RC_PROJECT_DIR")
            or os.getcwd()
        ).resolve()
    )


def _run_rc_reconcile(project_dir: str) -> tuple[int, str, str]:
    """Run `rc reconcile` and return (returncode, stdout, stderr).

    Reconcile findings go to stdout. The return code alone distinguishes
    "clean" (0) from "MCP-skip detected" (1). Errors (missing git, missing
    audit root) also return non-zero — we treat those separately.
    """
    rc_cli = _PROJECT_ROOT / "src" / "rc_cli.py"
    if not rc_cli.is_file():
        return (0, "", "rc_cli.py not found")
    try:
        result = subprocess.run(
            [sys.executable, str(rc_cli), "reconcile", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_dir,
        )
        return (result.returncode, result.stdout, result.stderr)
    except Exception as exc:
        return (0, "", f"reconcile failed: {exc}")


def _parse_reconcile_output(stdout: str) -> list[str]:
    """Parse `rc reconcile --json` output into a list of missing files."""
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return list(data.get("missing", []))
        if isinstance(data, list):
            return [str(x) for x in data]
    except ValueError:
        pass
    return []


def _resolve_rc_mode(project_dir: str) -> str:
    """Resolve RC_MODE from env, falling back to the project's .envrc.local.

    Claude Code spawns hooks in subshells where direnv may not have loaded.
    Read the .envrc.local block directly so the copilot block written by
    `rc enable-enforcement` is honored at hook time.
    """
    mode = os.environ.get("RC_MODE", "").strip().lower()
    if mode:
        return mode
    envrc_local = Path(project_dir) / ".envrc.local"
    if not envrc_local.is_file():
        return "advise"
    try:
        text = envrc_local.read_text(encoding="utf-8")
    except OSError:
        return "advise"
    import re
    # Look for RC_MODE=copilot or RC_MODE=advise inside the fenced block
    block_match = re.search(
        r"# >>> rc enforcement >>>(.*?)# <<< rc enforcement <<<",
        text, re.DOTALL,
    )
    if not block_match:
        return "advise"
    block = block_match.group(1)
    m = re.search(r"^export\s+RC_MODE=(\w+)", block, re.MULTILINE)
    if m:
        return m.group(1).lower()
    return "advise"


def main() -> None:
    payload = _read_payload()

    # Honor Claude Code retry signal: if this is a retry, approve to avoid
    # infinite blocking loops.
    if payload.get("stop_hook_active") is True:
        sys.exit(0)

    project_dir = _project_dir(payload)
    rc_mode = _resolve_rc_mode(project_dir)

    rc, stdout, stderr = _run_rc_reconcile(project_dir)
    missing = _parse_reconcile_output(stdout)

    # Reconcile errored (missing git, missing audit root, etc.). Treat as
    # "could not verify" — log to stderr but don't block, to avoid false
    # positives from infrastructure failures.
    if rc == 0 and not missing:
        # Clean
        sys.exit(0)

    if rc not in (0, 1):
        # Infrastructure error (missing git, missing audit root, etc.)
        if rc_mode in ("copilot", "autopilot"):
            # Copilot mode: fail closed — refuse to approve session end
            # when verification infrastructure is unavailable.
            sys.stderr.write(
                f"[reasoning-core] reconcile infrastructure unavailable "
                f"(rc={rc}); cannot verify MCP-skip. Refusing session end "
                f"in copilot mode.\n"
            )
            sys.exit(2)
        # Advisory: log only
        sys.stderr.write(f"[reasoning-core] reconcile error: {stderr.strip()}\n")
        sys.exit(0)

    # MCP-skip detected
    summary = f"files written without gate_edit call: {', '.join(missing[:5])}"
    if len(missing) > 5:
        summary += f" (+{len(missing) - 5} more)"

    if rc_mode in ("copilot", "autopilot"):
        # In copilot mode, block the session end.
        # Claude Code Stop hook protocol: on exit code 2, the JSON decision
        # on stdout is ignored; only stderr is fed back to the model.
        # So we exit 2 with a clean stderr message and emit nothing to stdout.
        msg = (
            f"MCP-SKIP DETECTED: {summary}. "
            f"All file writes must go through gate_edit. "
            f"Do not bypass."
        )
        sys.stderr.write(f"[reasoning-core] {msg}\n")
        sys.exit(2)
    else:
        # Advisory: log to stderr only (operator sees it)
        sys.stderr.write(
            f"[reasoning-core] advisory MCP-skip: {summary}\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()