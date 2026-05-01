#!/usr/bin/env python3
"""Claude Code PreToolUse hook for the Bash tool.

Wired by `.claude/settings.json` (matcher `Bash`). Blocks shell commands that
would rewrite source files inside the project (the path Claude takes when it
wants to dodge the Edit/Write hook), kill the sidecar, or wipe the guard
configuration.

Exit codes:
  0 — command allowed (read-only, build/test, package install in venv, etc.)
  2 — command blocked; stderr explains why
  0 — also returned on malformed stdin (don't block on bad payloads)

Override: ``RC_ALLOW_GUARD_EDIT=1`` lets through commands that touch the
guard files / sidecar process. Same env knob as the Edit-side lock so the
override is single-toggle.

Implementation rules mirror pre_edit_guard.py:
  - Zero third-party deps (stdlib only).
  - Conservative: when in doubt, allow — this hook is precision, not recall.
    The Edit/Write hook is the recall layer.
  - Fast (<5s budget set in settings.json).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# Hooks dir on sys.path for shared audit_log import.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402

ALLOW_OVERRIDE_ENV = "RC_ALLOW_GUARD_EDIT"

# Filename / path fragments that, when targeted by a write op, are denied.
GUARDED_PATH_FRAGMENTS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    "src/hooks/pre_edit_guard.py",
    "src/hooks/pre_bash_guard.py",
    "src/s2_core.py",
    "src/grammars.py",
    "src/ssm_backbone.py",
    "src/mcp_reasoner.py",
    "scripts/start-sidecar.sh",
)

# Process names that must not be killed via Bash.
GUARDED_PROCESS_TOKENS = (
    "s2_core",
    "mcp_reasoner",
    "start-sidecar",
)

# Commands that we always reject as obvious bypass attempts.
HARD_DENY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # pkill / kill / killall against our sidecar / mcp processes
    re.compile(r"\b(?:p?kill(?:all)?|launchctl\s+(?:unload|kill))\b.*\b(?:s2_core|mcp_reasoner|start-sidecar)\b"),
    # Disable hooks via writing to settings.json with shell redirection
    re.compile(r"(?:>|>>|tee\b).*\.claude/settings(?:\.local)?\.json"),
    # rm against settings or guard files
    re.compile(r"\brm\b[^|;&]*\.claude/settings(?:\.local)?\.json"),
    re.compile(r"\brm\b[^|;&]*src/hooks/"),
    # chmod -x on the hook script
    re.compile(r"\bchmod\b[^|;&]*-x[^|;&]*src/hooks/"),
    # unset of the safety env var
    re.compile(r"\bunset\s+(?:S2_FAIL_CLOSED|RC_ALLOW_GUARD_EDIT)\b"),
)

# Source-write patterns: shell-level writes that target source files inside
# the project. The list is intentionally narrow — only obvious shell-write
# constructs against tracked source extensions. We do not try to be a full
# shell parser; we are a tripwire.
SRC_EXTENSIONS = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".cs", ".sql", ".sh", ".json", ".yaml", ".yml",
    ".md", ".markdown", ".mdx", ".toml", ".ini",
)
_SRC_EXT_PATTERN = "|".join(re.escape(e) for e in SRC_EXTENSIONS)
SRC_WRITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `> path.py`, `>> path.py`, `1> path.py`, `&> path.py`
    re.compile(rf"(?<![<>0-9&])(?:1\s*|2\s*|&\s*)?>\s*['\"]?[^|;&\s]*({_SRC_EXT_PATTERN})\b"),
    re.compile(rf">>\s*['\"]?[^|;&\s]*({_SRC_EXT_PATTERN})\b"),
    # `tee path.py`, `tee -a path.py`
    re.compile(rf"\btee\b[^|;&]*\s['\"]?[^|;&\s]*({_SRC_EXT_PATTERN})\b"),
    # `sed -i ... path.py`
    re.compile(rf"\bsed\b[^|;&]*-i\b[^|;&]*({_SRC_EXT_PATTERN})\b"),
    # `python -c "open('x.py','w').write(...)"`
    re.compile(r"""(?:python(?:3)?|node)\b[^|;&]*-c\b[^|;&]*open\s*\(\s*['"][^'"]+['"]\s*,\s*['"][wa]['"]"""),
    # `python -c "...writeFileSync(...)"`  (node)
    re.compile(r"""(?:node)\b[^|;&]*-e\b[^|;&]*write(?:FileSync|file)\s*\("""),
    # `cat <<EOF > path.py` heredocs
    re.compile(rf"<<\s*['\"]?\w+['\"]?[^|;&]*>\s*['\"]?[^|;&\s]*({_SRC_EXT_PATTERN})\b"),
)

# Always-allow commands. If the command starts with one of these tokens and
# does not contain a write redirection, we let it through without scanning.
# Captures common dev workflow: tests, builds, package managers (which write
# only to lockfiles / .venv / node_modules — those are not in SRC_EXTENSIONS).
SAFE_LEADING_TOKENS = (
    "ls", "cat", "head", "tail", "grep", "rg", "find", "fd",
    "git",  # git itself never touches source via shell — Claude must use Edit/Write
    "pytest", "python", "python3", "uv", "ruff", "mypy", "black", "isort",
    "node", "npm", "npx", "yarn", "pnpm", "bun",
    "dotnet", "go", "cargo", "rustc",
    "echo", "printf", "test", "true", "false",
    "curl", "wget", "ping", "host", "dig",
    "ps", "df", "du", "uname", "uptime", "whoami", "pwd",
    "which", "type", "command",
    "make", "cmake", "ninja",
    "docker", "kubectl",
)


def _read_payload() -> Optional[Dict[str, Any]]:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _exit(code: int, *messages: str) -> None:
    for msg in messages:
        if msg:
            sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def _override_active() -> bool:
    return os.environ.get(ALLOW_OVERRIDE_ENV) == "1"


def _command_first_token(cmd: str) -> str:
    stripped = cmd.lstrip()
    # Strip env-prefix assignments: `FOO=bar BAZ=qux real_cmd ...`
    while True:
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+", stripped)
        if not m:
            break
        stripped = stripped[m.end():]
    # Strip simple sudo prefix.
    if stripped.startswith("sudo "):
        stripped = stripped[5:].lstrip()
    token = re.split(r"\s|;|&|\|", stripped, maxsplit=1)[0]
    # Drop ./ or absolute path leading dirs to get the basename.
    return os.path.basename(token)


def _hard_deny_reason(cmd: str) -> Optional[str]:
    for pat in HARD_DENY_PATTERNS:
        if pat.search(cmd):
            return f"hard-deny pattern: {pat.pattern}"
    return None


def _guarded_path_match(cmd: str) -> Optional[str]:
    for frag in GUARDED_PATH_FRAGMENTS:
        if frag in cmd:
            return frag
    return None


def _src_write_match(cmd: str) -> Optional[str]:
    for pat in SRC_WRITE_PATTERNS:
        m = pat.search(cmd)
        if m:
            return pat.pattern
    return None


def _is_safe_leading(cmd: str) -> bool:
    head = _command_first_token(cmd)
    return head in SAFE_LEADING_TOKENS


def screen_command(cmd: str) -> tuple[int, str]:
    """Return (exit_code, message). 0 = allow, 2 = block."""
    cmd = cmd.strip()
    if not cmd:
        return 0, ""

    # Layer A: hard-deny patterns trump everything except the explicit override.
    deny_reason = _hard_deny_reason(cmd)
    if deny_reason:
        if _override_active():
            return 0, f"[hybrid-reasoner] override: hard-deny pattern allowed via {ALLOW_OVERRIDE_ENV}=1 ({deny_reason})"
        return 2, (
            "[hybrid-reasoner] BLOCKED: shell command targets the guard or sidecar.\n"
            f"  matched: {deny_reason}\n"
            f"  command: {cmd[:300]}\n"
            f"  override: set {ALLOW_OVERRIDE_ENV}=1 in your shell, restart Claude."
        )

    # Layer B: shell command targeting a guarded path (regardless of operation).
    guarded = _guarded_path_match(cmd)
    if guarded:
        # Allow read-only ops on guarded paths so Claude can `cat .claude/settings.json`.
        # Only block if the command also looks like a write or kill.
        if _src_write_match(cmd) or any(p.search(cmd) for p in HARD_DENY_PATTERNS):
            if _override_active():
                return 0, f"[hybrid-reasoner] override: guarded-path write allowed via {ALLOW_OVERRIDE_ENV}=1 ({guarded})"
            return 2, (
                "[hybrid-reasoner] BLOCKED: shell write to guard file.\n"
                f"  guarded path: {guarded}\n"
                f"  command: {cmd[:300]}\n"
                f"  override: set {ALLOW_OVERRIDE_ENV}=1 in your shell, restart Claude."
            )

    # Layer C: kill/pkill against sidecar process names.
    for token in GUARDED_PROCESS_TOKENS:
        if re.search(r"\b(?:p?kill|killall)\b[^|;&]*" + re.escape(token), cmd):
            if _override_active():
                return 0, f"[hybrid-reasoner] override: process kill allowed via {ALLOW_OVERRIDE_ENV}=1 ({token})"
            return 2, (
                "[hybrid-reasoner] BLOCKED: shell kill targets sidecar process.\n"
                f"  process token: {token}\n"
                f"  command: {cmd[:300]}\n"
                f"  override: set {ALLOW_OVERRIDE_ENV}=1 in your shell, restart Claude."
            )

    # Layer D: shell-level source rewrite. The write-pattern check runs BEFORE
    # the safe-leading allowlist — `echo x > main.py` still blocks even though
    # `echo` is a safe leader. Only when no write pattern matches do we fall
    # through and use the allowlist as a "we don't know this command, allow it"
    # short-circuit.
    src_pat = _src_write_match(cmd)
    if src_pat:
        if _override_active():
            return 0, f"[hybrid-reasoner] override: shell write allowed via {ALLOW_OVERRIDE_ENV}=1 ({src_pat})"
        return 2, (
            "[hybrid-reasoner] BLOCKED: shell-level source write detected.\n"
            f"  pattern: {src_pat}\n"
            f"  command: {cmd[:300]}\n"
            "  reason: use the Edit / Write / MultiEdit tools so the SSM guard can score the change.\n"
            f"  override: set {ALLOW_OVERRIDE_ENV}=1 in your shell, restart Claude."
        )

    # No write pattern, no guard hit — let it through (with or without an
    # explicit safe-leading match; this hook is precision, not recall).
    return 0, ""


def main() -> None:
    started = time.time()
    payload = _read_payload()
    if payload is None:
        _exit(0)

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        _exit(0)

    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        _exit(0)

    code, msg = screen_command(cmd)
    decision = "blocked" if code == 2 else "allowed"
    try:
        audit_log.append_event(audit_log.new_event(
            tool_name="Bash",
            decision=decision,
            command=cmd[:512],
            latency_ms=int((time.time() - started) * 1000),
            reason=msg.splitlines()[0] if msg else "",
        ))
    except Exception:  # noqa: BLE001
        pass
    _exit(code, msg)


if __name__ == "__main__":
    main()
