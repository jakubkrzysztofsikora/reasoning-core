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
import shlex
import sys
import time
from typing import Any, Dict, List, Optional

# Hooks dir on sys.path for shared audit_log import.
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import audit_log  # type: ignore  # noqa: E402
import _git_lease  # type: ignore  # noqa: E402

ALLOW_OVERRIDE_ENV = "RC_ALLOW_GUARD_EDIT"

# Filename / path fragments that, when targeted by a write op, are denied.
GUARDED_PATH_FRAGMENTS = (
    ".envrc",
    ".envrc.local",
    ".claude/settings.json",
    ".claude/settings.local.json",
    "src/hooks/pre_edit_guard.py",
    "src/hooks/pre_bash_guard.py",
    "src/s2_core.py",
    "src/grammars.py",
    "src/ssm_backbone.py",
    "src/mcp_reasoner.py",
    "scripts/start-sidecar.sh",
    ".reasoning-core/git_worktrees.yaml",
    "reasoning-core/git_lease",
)

# Process names that must not be killed via Bash.
GUARDED_PROCESS_TOKENS = (
    "s2_core",
    "mcp_reasoner",
    "start-sidecar",
)

# Commands that we always reject as obvious bypass attempts.
#
# Round-5 known limits (NOT in scope for the regex set; documented
# for honest disclosure):
#
# * APFS case-folding (macOS): ``.ENVRC.local`` and ``.EnvRc.LOCAL``
#   are the same file on APFS (case-insensitive, case-preserving).
#   The regex set is case-sensitive; APFS bypass is a known limit.
#   Mitigation: enforce case in the filesystem, or use a HFS+/ext4
#   mount for the guard-protected paths.
# * Audit log chain: the audit log is append-only at the file level
#   but the file itself is rewritable by anyone with write access
#   to its parent directory. Out of scope for the regex set.
# * Symlink races: the symlink-resolver uses ``os.path.realpath``
#   at evaluation time; a TOCTOU between resolve and the actual
#   write is a known limit. The string-level guard catches the
#   common case (redirect into a symlink whose target resolves to
#   a guarded path); atomic races are not closed by this layer.
#
# The regex set closes the high-frequency bypass vectors identified
# in the 2026-09-22 hostile re-review (round-2 Finding 5 + the
# round-5 follow-up). The known-limit list is the residual surface
# that requires filesystem-level or OS-level mitigations.

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
    # Agent self-arming `rc bypass-next` / direct kill-switch JSON edit.
    # Reviewer-flagged P0 vuln: agent could shell out to arm a one-shot
    # bypass and immediately follow with a guarded edit. Block both the
    # shim and the underlying python invocation.
    re.compile(r"\brc\s+bypass-next\b"),
    re.compile(r"\bsrc\.rc_cli\b.*bypass-next\b"),
    # Round-5 RC-SEC-PATH-FORM: the path-form ``python3 src/rc_cli.py
    # bypass-next`` and ``python src/rc_cli.py bypass-next`` slip the
    # above regex set. Match any python invocation that points at
    # src/rc_cli.py followed by bypass-next on the same argv.
    re.compile(r"\bpython\w*\s+[^|;&]*src/rc_cli\.py[^|;&]*\bbypass-next\b"),
    re.compile(r"\bkill_switches\.json\b"),
    # Round-5 RC-SEC-ENVRC-LOCAL: .envrc.local is the per-project
    # downgrade channel (read by stop_reconcile.py). One append
    # permanently downgrades the next session to warn-only. Block
    # shell redirects and Python/Node writes to .envrc.local.
    re.compile(r"(?:>|>>|tee\b)\s*[^|;&]*\.envrc\.local\b"),
    re.compile(r"\b(?:echo|cat|printf|sed)\b[^|;&]*>\s*[^|;&]*\.envrc\.local\b"),
    re.compile(r"\b(?:writeFileSync|writeFile|write_text|fwrite)\b[^|;&]*\.envrc\.local\b"),
    re.compile(r"\bPath\(['\"]\\?\.?/?[^'\"]*\\?\.envrc\.local['\"]\)\.(?:write_text|write_bytes)"),
    # RC-SEC-04: Copy-tool family — deny dd, ln, rsync against guarded paths.
    # These bypass the source-extension / guarded-fragment matching in the
    # copy pattern because they accept arbitrary destinations.
    re.compile(r"\bdd\b[^|;&]*\bof\s*=\s*[^|;&]*(?:\.envrc(?:\.local)?|\.claude/settings|src/hooks/|kill_switches)"),
    re.compile(r"\bln\s+(?:-sf?|--symbolic)\b[^|;&]*(?:\.envrc(?:\.local)?|\.claude/settings|src/hooks/|kill_switches)"),
    re.compile(r"\brsync\b[^|;&]*(?:\.envrc(?:\.local)?|\.claude/settings|src/hooks/|kill_switches)"),
    # RC_BYPASS_NEXT=1 set on the same command line counts too.
    re.compile(r"\bRC_BYPASS_NEXT\s*=\s*1\b"),
    # RC-SEC-05: Git wrapper bypasses — deny indirect git invocations that
    # could execute arbitrary subcommands outside the allowlist.
    re.compile(r"\benv\s+.*\bgit\b"),           # env git <subcmd>
    re.compile(r"\btimeout\s+\d+\s+.*\bgit\b"), # timeout N git <subcmd>
    re.compile(r"/usr/bin/git\b"),               # absolute path git
    re.compile(r"\bsh\s+-c\s+.*\bgit\b"),       # sh -c "git ..."
    re.compile(r"\bbash\s+-c\s+.*\bgit\b"),     # bash -c "git ..."
    re.compile(r"\$\(.*\bgit\b"),                # $(git ...)
    re.compile(r"\$\([^)]*\bgit\b"),              # multiline $( git ... )
    re.compile(r"`[^`]*\bgit\b"),                 # `git ...`
    # RC-SEC-05: Individual git deny patterns removed — replaced by
    # subcommand allowlist (Layer A2 in screen_command).
    # Round-5 RC-SEC-NODE-UPPERCASE: node -E (uppercase E) is
    # the same as node -e but with extended regex; the existing
    # regex only catches lowercase. Block both.
    re.compile(r"\bnode\s+-E\b"),
    # Round-5 RC-SEC-PKILL-FREEZE: pkill -STOP -f <process> is
    # not a kill -- it's a freeze. The sidecar continues to exist
    # (so S2_FAIL_CLOSED never trips) but no longer processes
    # requests. Block any pkill/kill against the sidecar's process
    # title regardless of signal.
    re.compile(r"\b(?:p?kill(?:all)?|kill\s+-STOP)\b[^|;&]*\b(?:reasoning-core-sidecar|s2_core|mcp_reasoner|start-sidecar)\b"),
    # Reviewer-flagged (3-reviewer convergent on commit c2cc135):
    # bare `\bRC_*\s*=\s*\S` over-blocks heredoc bodies, doc generators,
    # and comments containing the literal string. Anchored on COMMAND
    # POSITION (start of cmd, after `;`/`&&`/`||`/newline, optionally
    # preceded by `export`). The variable name alone is the trip signal —
    # value irrelevant to the threat model.
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_DRIFT_OVERRIDE\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_LANG_OVERRIDE\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_ALLOW_GUARD_EDIT\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_ALLOW_SUBAGENT_GUARD_EDIT\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_SHADOW_MODE\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?S2_FAIL_CLOSED\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_LANG_LOCK\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_PLAN_BLOCK\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_MOCK_DETECTOR\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_PLAN_QUALITY\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_DRIFT_(?:DENY|WARN)\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_RISK_DIM_THRESHOLD\s*="),
    re.compile(r"(?:^|[\n;]|&&|\|\|)\s*(?:export\s+)?RC_COHERENCE_THRESHOLD\s*="),
    # Re-audit-hostile/2026-09-19-reaudit-fixes (RC-SEC-03): shell-decode
    # payload patterns. The previous regex only caught `base64 -d | bash|sh`.
    # macOS uses `-D`, GNU uses `--decode`, and other decoders (openssl, xxd,
    # python -c "import base64; ...") achieve the same outcome. Block any
    # `... | <decoder> | <interpreter>` chain where the interpreter is one of
    # the dynamic-execution sinks.
    re.compile(r"\bbase64\b[^|;&]*-[dD]\b[^|;&]*\|\s*(?:bash|sh|zsh|eval|python|python3|node|perl|ruby)\b"),
    re.compile(r"\bbase64\b[^|;&]*--decode\b[^|;&]*\|\s*(?:bash|sh|zsh|eval|python|python3|node|perl|ruby)\b"),
    re.compile(r"\bopenssl\b[^|;&]*-d\b[^|;&]*\|\s*(?:bash|sh|zsh|eval|python|python3|node)\b"),
    re.compile(r"\bxxd\b[^|;&]*-r\b[^|;&]*\|\s*(?:bash|sh|zsh|eval|python|python3|node)\b"),
    re.compile(r"\bzstd\b[^|;&]*-d\b[^|;&]*\|\s*(?:bash|sh|zsh|eval|python|python3|node)\b"),
    # The classic "agent prefixes a real command with override" form:
    # `RC_X=val real_command args` — same anchor catches it.
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
    # `cp|mv|install|rsync <src> <dest>` where dest is a tracked source ext.
    # Audit-hostile/2026-09-19-fixes: lifted from the lang-lock-only path.
    # Re-audit-hostile/2026-09-19-reaudit-fixes (RC-SEC-02): also fire when
    # the destination is a guarded path fragment (e.g. `src/hooks/` even
    # without a file extension) so `cp /tmp/x.py src/hooks/` trips the base
    # guard regardless of RC_LANG_LOCK or dest filename.
    re.compile(rf"\b(?:cp|mv|install|rsync)\b[^|;&]+\s([^|;&\s]+(?:{_SRC_EXT_PATTERN}))\b"),
    re.compile(r"""\b(?:cp|mv|install|rsync)\b\s+\S+\s+(?:src/hooks|src/s2_core|src/grammars|src/ssm_backbone|src/mcp_reasoner|scripts/start-sidecar|\.claude/settings(?:\.local)?\.json)"""),
    # `pathlib.Path("x.py").write_text(...)` — modern idiom,
    # not covered by the open() regex above.
    re.compile(r"""pathlib\.Path\s*\([^)]+\)\.(?:write_text|write_bytes)\s*\("""),
    # Re-audit-hostile/2026-09-19-reaudit-fixes (RC-SEC-04): pathlib aliased
    # as `from pathlib import Path; Path('x.py').write_text(...)`. Catch
    # both `Path(...)` and `pathlib.Path(...)` writer methods.
    re.compile(r"""\bPath\s*\([^)]+\)\.(?:write_text|write_bytes|write_lines)\s*\("""),
    # `open('x.py', mode='w')` keyword-arg form (re-audit RC-SEC-04).
    re.compile(r"""open\s*\(\s*['"][^'"]+['"]\s*,\s*mode\s*=\s*['"][wa]['"]"""),
    # `open('x.py', 'wb')` second-positional binary form.
    re.compile(r"""open\s*\(\s*['"][^'"]+['"]\s*,\s*['"][wa][bx]?['"]"""),
    # RC-SEC-05: Git write patterns removed — handled by subcommand allowlist.
    # Re-audit-hostile/2026-09-19-reaudit-fixes (RC-SEC-04): heredoc stdin
    # execution of python/node/perl/ruby. Trip when a guarded path fragment
    # appears ANYWHERE in the heredoc body so an agent cannot smuggle a
    # `open(...)` or `Path(...).write_text(...)` past the regex by piping
    # via stdin. The fragment match alone is the trip signal.
    re.compile(r"""(?:python|python3|node|perl|ruby)\s*<<\s*['"]?\w+['"]?[\s\S]*?(?:src/hooks|src/s2_core|src/grammars|src/ssm_backbone|src/mcp_reasoner|scripts/start-sidecar|\.claude/settings(?:\.local)?\.json)"""),
    # `base64 -d | bash|sh|zsh|eval` payload obfuscation pattern.
    re.compile(r"""base64\b[^|;&]*-d\b[^|;&]*\|\s*(?:bash|sh|zsh|eval)\b"""),
    # `$(echo ... | base64 -d)` alternate obfuscation form.
    re.compile(r"""\$\(\s*echo\b[^)]*\|\s*base64\b[^)]*-d\b[^)]*\)"""),
)

# Always-allow commands. If the command starts with one of these tokens and
# does not contain a write redirection, we let it through without scanning.
# Captures common dev workflow: tests, builds, package managers (which write
# only to lockfiles / .venv / node_modules — those are not in SRC_EXTENSIONS).
# RC-SEC-05: Git subcommand allowlist. Only these read-only / inspection
# commands are permitted. Everything else denied (rc=2).
# Compound subcommands (e.g., "stash pop") must be listed explicitly.
# Dangerous subcommands removed: reflog (can destroy history), config (can
# disable hooks via core.hooksPath), remote (can push to arbitrary remotes).
GIT_ALLOWED_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "rev-parse",
    "ls-files", "ls-tree", "blame", "describe", "grep",
    "help", "version", "--version",
    # Read-only stash/worktree variants
    "stash list", "stash show",
    "worktree list",
    # Commit flow on the current branch. Branch/worktree switching stays denied.
    "add", "commit", "push", "fetch",
})

GIT_EXTRA_ALLOW_ENV = "RC_GIT_ALLOW"

_GIT_UNSAFE_FLAGS = {
    "commit": (frozenset({"--no-verify"}), "n"),
    "push": (frozenset({"--force", "--force-with-lease", "--force-if-includes",
                        "--delete", "--mirror", "--no-verify", "--prune"}), "fd"),
}


def _git_allowed_subcommands() -> frozenset:
    extra = os.environ.get(GIT_EXTRA_ALLOW_ENV, "")
    return GIT_ALLOWED_SUBCOMMANDS | {s.strip() for s in extra.split(",") if s.strip()}


def _git_unsafe_flag(subcmd: str, args: list) -> Optional[str]:
    """Return the first flag/refspec that would skip hooks or rewrite remote history."""
    if subcmd not in _GIT_UNSAFE_FLAGS:
        return None
    long_flags, short_chars = _GIT_UNSAFE_FLAGS[subcmd]
    for arg in args:
        if arg.split("=", 1)[0] in long_flags:
            return arg
        if arg.startswith("-") and not arg.startswith("--") and set(arg[1:]) & set(short_chars):
            return arg
        if subcmd == "push" and arg[:1] in ("+", ":"):
            return arg
    return None


_SHELL_FED = re.compile(
    r"\b(?:ba|z|k|da|fi)?sh\b|\bsource\b|\beval\b|\bxargs\b|\bexec\b"
    r"|\bpython[\d.]*\b|\bperl\b|\bruby\b|\bnode\b|\benv\b|(?:^|[\s;&|(])\.\s"
)


_COMMIT_MSG_HEREDOC = re.compile(r"\Agit commit(?: -[a-zA-Z]+)* -F - <<'(\w+)'\n")


def _drop_data_heredocs(cmd: str) -> str:
    """Drop the body of a lone `git commit -F - <<'X'` message heredoc.

    Every other heredoc body stays in place and gets screened.
    """
    m = _COMMIT_MSG_HEREDOC.match(cmd)
    if not m:
        return cmd
    lines = cmd[m.end():].split("\n")
    if m.group(1) not in lines or lines.index(m.group(1)) != len(lines) - 1 - (lines[-1] == ""):
        return cmd
    return cmd[:m.end() - 1]


def _shell_split(cmd: str) -> list:
    """Split on &&, ||, ;, |, newline outside quotes; heredoc bodies dropped."""
    return _split_with_quote(cmd)[0]


def _split_with_quote(cmd: str) -> tuple:
    cmd = _drop_data_heredocs(cmd)
    out, buf, quote, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1])
                i += 1
        elif c == "\\" and i + 1 < len(cmd):
            buf.append(c + cmd[i + 1])
            i += 1
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            while i + 1 < len(cmd) and cmd[i + 1] != "\n":
                i += 1
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c in ";|\n" or (c == "&" and cmd[i + 1:i + 2] != ">" and cmd[i - 1:i] not in "<>"):
            out.append("".join(buf))
            buf = []
            if c in "&|" and cmd[i + 1:i + 2] == c:
                i += 1
        else:
            buf.append(c)
        i += 1
    out.append("".join(buf))
    return out, quote


_ENV_ASSIGN = re.compile(r"""^[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|"(?:\\.|[^"\\])*"|\\.|[^\s'"\\])*\s+""")


def _git_segments(cmd: str) -> list:
    """(segment, cwd_known) per git invocation, env-assignment prefixes stripped.

    cwd_known turns False once the chain changes directory.
    """
    out, cwd_known = [], True
    for seg in _shell_split(cmd):
        seg = seg.strip().lstrip("({ ").rstrip(")} ")
        while (m := _ENV_ASSIGN.match(seg)):
            seg = seg[m.end():]
        if re.match(r"^(?:cd|pushd|popd)\b", seg):
            cwd_known = False
        if re.match(r"git(?:\s|$)", seg):
            out.append((seg, cwd_known))
        elif _SHELL_FED.search(seg):
            for q in re.finditer(r"'([^']*)'|\"((?:\\.|[^\"\\])*)\"", seg):
                out.extend((s, False) for s, _ in _git_segments(q.group(1) or q.group(2) or ""))
    return out


def _extract_git_subcommand(cmd: str) -> Optional[str]:
    """Extract git subcommand, handling -C <path> prefixes.
    
    Denies -c key=val overrides entirely — they can enable arbitrary code
    execution (e.g., core.fsmonitor=<script>).
    
    For compound subcommands (e.g., "stash pop"), returns both tokens.
    
    Examples:
      'git status' → 'status'
      'git stash pop' → 'stash pop'
      'git -C /repo log -1' → 'log'
      'git -c user.name=x commit' → None (denied)
      'git --help' → '--help'
    """
    stripped = cmd.lstrip()
    if not stripped.startswith("git"):
        return None
    
    # Remove 'git' prefix
    rest = stripped[3:].lstrip()
    
    # Skip -C <path> prefixes but DENY -c key=val overrides
    while rest.startswith("-"):
        if rest.startswith("-C "):
            # Skip -C flag and its argument (safe: just changes working dir)
            parts = rest.split(None, 2)
            if len(parts) >= 3:
                rest = parts[2].lstrip()
            elif len(parts) == 2:
                return None  # Malformed
            else:
                return None
        elif rest.startswith("-c "):
            # DENY: -c can set core.fsmonitor=<script> for arbitrary code exec
            return None
        elif rest.startswith("--"):
            # Long option like --help, --version
            parts = rest.split(None, 1)
            return parts[0]
        else:
            # Short option like -p, -n
            parts = rest.split(None, 1)
            if len(parts) == 1:
                return None
            rest = parts[1].lstrip()
            break
    else:
        pass
    
    if not rest:
        return None
    
    # Extract first two tokens for compound subcommands
    parts = rest.split(None, 2)
    if len(parts) >= 2:
        compound = f"{parts[0]} {parts[1]}"
        if compound in _git_allowed_subcommands():
            return compound
    return parts[0]


_RISKY_GIT_MENTION = re.compile(
    r"(?:^|[\n;&|(`]|\$\()[ \t]*git(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+(?:worktree|merge|branch|reset|checkout|switch|rebase"
    r"|restore|clean|update-ref|symbolic-ref|tag|config|init|filter-branch|reflog|gc|stash|am|apply"
    r"|cherry-pick|revert|pull|push|commit)\b"
)


_GIT_WRAPPERS = {"command", "builtin", "exec", "env", "nohup", "time", "nice", "sudo",
                 "timeout", "stdbuf", "xargs", "caffeinate", "arch",
                 "if", "then", "else", "elif", "do", "while", "until", "!", "{", "("}
_SHELL_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "("}
_REDIRECT_WORD = re.compile(r"\d*(?:[<>]|&>)")


def _indirect_git(cmd: str) -> bool:
    """True when a segment runs git through a wrapper, path or quoting the parser skips."""
    for seg in _shell_split(cmd):
        raw = seg.strip().lstrip("({ ")
        while (m := _ENV_ASSIGN.match(raw)):
            raw = raw[m.end():]
        if re.match(r"git(?:\s|$)", raw):
            continue
        try:
            words = shlex.split(raw)
        except ValueError:
            words = raw.split()
        skip = False
        for w in words:
            if skip:
                skip = False
                continue
            if (m := _REDIRECT_WORD.match(w)):
                skip = m.end() == len(w)
                continue
            if w in _GIT_WRAPPERS and w not in _SHELL_KEYWORDS and any(
                    os.path.basename(x) == "git" for x in words):
                return True
            if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w) or w.startswith("-") or w.isdigit() or w in _GIT_WRAPPERS:
                continue
            if os.path.basename(w.lstrip("({")) == "git":
                return True
            break
    return False


def _git_denial(cmd: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return a denial reason for the first disallowed git invocation, else None."""
    allowed = _git_allowed_subcommands()
    segments = _git_segments(cmd)
    if segments and re.search(r"(?:^|[\s;&|(])GIT_[A-Z_]*=", cmd):
        return "GIT_* environment overrides not allowed with git"
    if re.search(r"\bgit\b", cmd) and (
            _drop_data_heredocs(cmd).count("<<") > (_drop_data_heredocs(cmd) != cmd)
            or _split_with_quote(cmd)[1]):
        return "git with a heredoc or unbalanced quote cannot be screened; split the command"
    if "\\\n" in cmd and re.search(r"\bgit\b", cmd.replace("\\\n", "")):
        return "no backslash-newline continuations in commands that may run git"
    if re.search(r"\bcase\b", cmd) and re.search(r"\bgit\b", cmd):
        return "no 'case' blocks in commands that may run git"
    if _indirect_git(cmd):
        return "run git directly as 'git ...' (no wrapper, path or quoting)"
    mentioned = len(_RISKY_GIT_MENTION.findall(_drop_data_heredocs(cmd)))
    if mentioned > sum(len(_RISKY_GIT_MENTION.findall(seg)) for seg, _ in segments):
        return "git invocation hidden from screening (quotes, heredoc or separators); split the command"
    verbs = [(_git_lease._parse_git_argv(seg, None) or (None, []))[1][:2] for seg, _ in segments]
    if ["worktree", "add"] in verbs and len(_shell_split(cmd)) > 1:
        return "'git worktree add' must run as its own command"
    if any(v[:1] in (["branch"], ["merge"], ["worktree"]) for v in verbs) and re.search(r"[(){}]", cmd):
        return "no shell grouping around 'git branch/merge/worktree'; run it plainly"
    if any(v[:1] == ["merge"] for v in verbs) and len(_shell_split(cmd)) > 1:
        return "'git merge' must run as its own command"
    for seg, cwd_known in segments:
        verdict = _git_lease.check_git_segment(seg, (cwd or os.getcwd()) if cwd_known else None)
        if verdict is not None:
            if not verdict[0]:
                return verdict[1]
            continue
        subcmd = _extract_git_subcommand(seg)
        if subcmd not in allowed:
            return f"subcommand '{subcmd or '<unknown>'}' not allowed"
        tokens = seg.split()
        head = subcmd.split()[-1]
        args = tokens[tokens.index(head) + 1:] if head in tokens else []
        flag = _git_unsafe_flag(subcmd, args)
        if flag:
            return f"'{subcmd} {flag}' not allowed (force/delete/hook-skip)"
    return None


SAFE_LEADING_TOKENS = (
    "ls", "cat", "head", "tail", "grep", "rg", "find", "fd",
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
    """Phase 1b: stdin via host-agnostic adapter; legacy fallback retained."""
    try:
        from src.hooks.adapters import claude as _claude_adapter  # type: ignore
        env = _claude_adapter.parse_stdin("PreToolUse")
        if env.tool_name is None and not env.raw:
            return None
        return dict(env.raw)
    except Exception:  # noqa: BLE001
        pass
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
    if code == 2:
        try:
            import audit_log  # type: ignore
            last = audit_log.last_event()
            decision_id = last.get("decision_id") if isinstance(last, dict) else None
            if decision_id:
                sys.stderr.write(
                    f"\n[hybrid-reasoner] Decision ID: {decision_id}\n"
                    f"  Inspect: rc explain {decision_id}\n"
                    f"  Override: rc bypass-next\n"
                )
        except Exception:
            pass  # Never let audit-log failure suppress the block message
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


_DESTRUCTIVE_WORDS = {"rm", "rmdir", "unlink", "mv", "shred", "truncate", "ln", "tee"}
_COPY_WORDS = {"cp", "install", "rsync"}
_OUT_REDIRECT = re.compile(r"\d*(?:&>>?|>>?\|?|<>)")


def _guarded_fragment_at(path: str) -> Optional[str]:
    real = os.path.realpath(path)
    for frag in GUARDED_PATH_FRAGMENTS:
        parts = frag.split("/")
        if frag in real or any(real.endswith("/" + "/".join(parts[:i])) and os.path.exists(os.path.join(real, *parts[i:]))
                               for i in range(1, len(parts))):
            return frag
        # ponytail: ancestors above the fragment's own dirs only at the project root; deeper `rm -rf ..` escapes.
        if os.path.isdir(real) and os.path.exists(os.path.join(real, frag)):
            return frag
    return None


def _cwd_guarded_operand(cmd: str, cwd: Optional[str]) -> Optional[str]:
    """Guarded fragment hit by a destructive operand resolved against cwd (and any `cd` in the chain)."""
    heres = {cwd or os.getcwd()}
    for seg in re.split(r"&&|\|\||[;|&\n]", cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            continue
        while words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        if not words:
            continue
        if words[0] == "cd":
            # ponytail: a cd may fail (`cd /nope || rm x`), so every directory seen stays a candidate.
            heres |= {os.path.join(h, os.path.expanduser(words[1] if len(words) > 1 else "~")) for h in heres}
            continue
        head = os.path.basename(words[0])
        destructive = head in _DESTRUCTIVE_WORDS
        if head in _COPY_WORDS:
            words = [words[0], words[-1]]
            destructive = len(words) > 1
        for i, w in enumerate(words[1:], 1):
            redirect_target = bool(_OUT_REDIRECT.fullmatch(words[i - 1]))
            if (m := _OUT_REDIRECT.match(w)) and m.end() < len(w):
                w, redirect_target = w[m.end():], True
            if head == "dd" and w.startswith("of="):
                w, redirect_target = w[3:], True
            if (destructive and not w.startswith("-")) or redirect_target:
                for here in heres:
                    hit = _guarded_fragment_at(os.path.join(here, os.path.expanduser(w)))
                    if hit:
                        return hit
    return None


# Patterns that pull a candidate redirect target out of a shell command. Each
# captures the path (without surrounding quotes) into group 1.
_REDIRECT_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `> path`, `>> path`, `1> path`, `&> path`, `2> path`, `1>> path`
    re.compile(r"""(?:>|>>|1>|2>|1>>|2>>|&>)\s*['"]?([^'"|;&\s]+)"""),
    # `tee path`, `tee -a path`
    re.compile(r"""\btee\b[^|;&]*\s+['"]?([^'"|;&\s]+)"""),
    # `sed -i ... path`
    re.compile(r"""\bsed\b[^|;&]*-i\b[^|;&]*['"]?([^'"|;&\s]+?)(?:['"]|$)"""),
    # `python -c "...open('path', 'w')..."`
    re.compile(r"""open\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"][wa]['"]"""),
    # `pathlib.Path('path').write_text/write_bytes(...)`
    re.compile(r"""[Pp]ath\s*\(\s*['"]([^'"]+)['"]\s*\)\.(?:write_text|write_bytes|write_lines)"""),
    # `cp|mv|install|rsync <src> <dest>` — second arg is the target.
    re.compile(r"""\b(?:cp|mv|install|rsync)\b\s+\S+\s+['"]?([^'"|;&\s]+)"""),
)


def _resolve_symlink_to_guarded(cmd: str) -> Optional[str]:
    """RC-SEC-05: if a redirect target resolves through a symlink to a
    guarded file, return the guarded fragment; else None.

    Only returns a hit when:
      - the candidate path exists on disk AND
      - os.path.realpath() resolves to an absolute path that contains one
        of the GUARDED_PATH_FRAGMENTS as a substring.
    Failure modes (permission errors, missing files, weird filesystems)
    return None so the regex layer still catches the obvious cases.
    """
    import os
    for pat in _REDIRECT_TARGET_PATTERNS:
        for m in pat.finditer(cmd):
            target = m.group(1).strip()
            if not target or target.startswith(("/", "~")) is False and "/" not in target:
                # Skip bare filenames without slashes; not a symlink-overwrite
                # vector (would be in cwd which we trust by default).
                continue
            try:
                # Expand ~ if present.
                expanded = os.path.expanduser(target)
                if not os.path.exists(expanded):
                    continue
                real = os.path.realpath(expanded)
                for frag in GUARDED_PATH_FRAGMENTS:
                    if frag in real:
                        return frag
            except OSError:
                # Permission denied, symlink loop, etc. — don't false-positive.
                continue
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


_LANG_LOCK_WRITE_RE = re.compile(
    r"(?:>\s*|>>\s*|tee\b[^|;&]*\s|sed\b[^|;&]*-i\b[^|;&]*|<<\s*[A-Za-z_]+\s+>\s*)"
    r"['\"]?([^|;&\s]*?\.[A-Za-z]{1,6})\b"
)
# Reviewer-flagged Bash escape gap: cp / mv / install / rsync / python -c
# open() / node -e writeFileSync route around the > redirect family.
_LANG_LOCK_COPY_RE = re.compile(
    r"\b(?:cp|mv|install|rsync)\b[^|;&]+\s(\S+\.[A-Za-z]{1,6})(?:\s|$|;|\|)"
)
_LANG_LOCK_PY_OPEN_RE = re.compile(
    r"""(?:python(?:3)?)\b[^|;&]*-c\b[^|;&]*open\s*\(\s*['"]([^'"]+\.[A-Za-z]{1,6})['"]\s*,\s*['"][wa]['"]"""
)
_LANG_LOCK_NODE_WRITE_RE = re.compile(
    r"""(?:node)\b[^|;&]*-e\b[^|;&]*writeFileSync\s*\(\s*['"]([^'"]+\.[A-Za-z]{1,6})['"]"""
)


def _manifest_disallowed_extension(cmd: str) -> Optional[str]:
    """If a Bash command writes to a path whose extension is outside the
    session manifest's declared language family, return the offending path.

    Closes the v2-plan-flagged escape: `Bash(cat > Tests/foo.py <<EOF)`
    bypasses Layer-3 lang-lock entirely without this check.
    """
    if os.environ.get("RC_LANG_LOCK") != "1":
        return None
    if os.environ.get("RC_LANG_OVERRIDE") == "1":
        return None
    try:
        # _session_manifest is in the same hooks/ directory.
        from pathlib import Path as _Path
        hooks_dir = _Path(__file__).resolve().parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import _session_manifest  # type: ignore
        cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        task_spec = os.environ.get("RC_TASK_SPEC") or ""
        key = _session_manifest.manifest_key(cwd, task_spec)
        mani = _session_manifest.load(key)
        if not mani:
            return None
        all_regexes = (
            _LANG_LOCK_WRITE_RE,
            _LANG_LOCK_COPY_RE,
            _LANG_LOCK_PY_OPEN_RE,
            _LANG_LOCK_NODE_WRITE_RE,
        )
        for rex in all_regexes:
            for m in rex.finditer(cmd):
                target = m.group(1)
                if not _session_manifest.is_path_allowed(mani, target):
                    return target
    except Exception:  # noqa: BLE001
        return None
    return None


def screen_command(cmd: str, cwd: Optional[str] = None) -> tuple[int, str]:
    """Return (exit_code, message). 0 = allow, 2 = block."""
    with _git_lease.deferred_reservations() as pending:
        code, msg = _screen_command(cmd, cwd)
    if code == 0 and pending:
        ok, why = _git_lease.reserve(pending)
        if not ok:
            return 2, f"[hybrid-reasoner] BLOCKED: git subcommand not allowed.\n  reason: {why}"
    return code, msg


def _screen_command(cmd: str, cwd: Optional[str] = None) -> tuple[int, str]:
    cmd = cmd.strip()
    if not cmd:
        return 0, ""

    # Layer L3-extension: Bash redirect to a path whose extension is outside
    # the session manifest's declared language family. Closes the escape
    # where `cat > Tests/foo.py <<EOF` bypasses pre_edit_guard's Layer-3
    # lang-lock by going through Bash instead of Edit/Write.
    bad_ext_path = _manifest_disallowed_extension(cmd)
    if bad_ext_path:
        return 2, (
            "[hybrid-reasoner] BLOCKED: shell write to a path outside this "
            "session's declared language family.\n"
            f"  path: {bad_ext_path}\n"
            "  fix: this repo was initialised for a different language. "
            "Either run `rc init --check` to refresh the manifest, or use the "
            "Edit / Write / MultiEdit tools so the SSM guard can score the change."
        )

    # Layer A: hard-deny patterns trump everything except the explicit override.
    deny_reason = _hard_deny_reason(cmd)
    if deny_reason:
        if _override_active():
            return 0, f"[hybrid-reasoner] override: hard-deny pattern allowed via {ALLOW_OVERRIDE_ENV}=1 ({deny_reason})"
        return 2, (
            "[hybrid-reasoner] BLOCKED: this command targets the guard, hook, "
            "or sidecar process and would disable the gate.\n"
            f"  matched: {deny_reason}\n"
            "  fix: there is no compliant way to do this from a shell command. "
            "If you genuinely need to bypass the guard (e.g. operator-initiated "
            "rollback of the framework itself), set RC_ALLOW_GUARD_EDIT=1 in the "
            "same command. The bypass is recorded in the audit log."
        )

    # Layer A2: RC-SEC-05 git subcommand allowlist. Git commands must use an
    # allowed subcommand. Everything else denied.
    git_denial = _git_denial(cmd, cwd)
    if git_denial:
        if _override_active():
            return 0, f"[hybrid-reasoner] override: git {git_denial} allowed via {ALLOW_OVERRIDE_ENV}=1"
        return 2, (
            f"[hybrid-reasoner] BLOCKED: git subcommand not allowed.\n"
            f"  reason: {git_denial}\n"
            f"  allowed: {', '.join(sorted(_git_allowed_subcommands()))}\n"
            "  fix: stage/commit/push on the current branch is permitted. "
            "Branch switching, history rewrites, force-push and hook skipping "
            "are not. Worktrees: open one listed in "
            f"{_git_lease.CONTRACT_REL} (git worktree add <path> -b <branch>), "
            "merge its branch from home, then git worktree remove <path>. "
            f"Operator can extend via {GIT_EXTRA_ALLOW_ENV}="
            "'checkout,rebase,abandon' in .envrc.local."
        )

    # Layer B: shell command targeting a guarded path (regardless of operation).
    # Two triggers: substring match (existing), or symlink resolution
    # (RC-SEC-05 re-audit) so `ln -s src/hooks/pre_bash_guard.py /tmp/x;
    # echo > /tmp/x` trips.
    guarded = _guarded_path_match(cmd)
    cwd_hit = _cwd_guarded_operand(cmd, cwd)
    if cwd_hit and not _override_active():
        return 2, (
            "[hybrid-reasoner] BLOCKED: shell write to a guarded file.\n"
            f"  guarded path: {cwd_hit}\n"
            "  fix: guarded files (and their directories) cannot be removed, moved "
            "or overwritten from a shell command, whatever the working directory or symlink used."
        )
    symlink_resolved = False
    if guarded is None:
        guarded = _resolve_symlink_to_guarded(cmd)
        if guarded is not None:
            symlink_resolved = True
    if guarded:
        # Allow read-only ops on guarded paths so Claude can `cat .claude/settings.json`.
        # Only block if the command also looks like a write or kill. The
        # symlink-resolved path is the exception: it ALREADY proves the
        # command intends to write to a guarded file via a non-source-
        # extension target (the entire attack the resolver was added to
        # stop), so any redirect into the symlink is by definition a write
        # and must be blocked even if the bare target filename has no
        # source extension. RC-SEC-05 closure.
        if symlink_resolved:
            if _override_active():
                return 0, f"[hybrid-reasoner] override: symlink-resolved guarded-path write allowed via {ALLOW_OVERRIDE_ENV}=1 ({guarded})"
            return 2, (
                "[hybrid-reasoner] BLOCKED: symlink overwrite of a guarded file.\n"
                f"  guarded path (realpath): {guarded}\n"
                "  fix: do not write through symlinks that resolve into "
                "src/hooks/, src/mcp/, the sidecar binary, the kill-switch "
                "state, or the agent settings. Use the Edit / Write / "
                "MultiEdit tool on the actual target file. Symlinks whose "
                "realpath lands on a guarded path are treated as writes "
                "regardless of the symlink's own filename or extension."
            )
        if (_src_write_match(cmd) or any(p.search(cmd) for p in HARD_DENY_PATTERNS)
                or re.search(r"(?:^|[\s;&|(])(?:rm|rmdir|unlink|mv|shred|truncate|ln)\b", cmd)):
            if _override_active():
                return 0, f"[hybrid-reasoner] override: guarded-path write allowed via {ALLOW_OVERRIDE_ENV}=1 ({guarded})"
            return 2, (
                "[hybrid-reasoner] BLOCKED: shell write to a guarded file.\n"
                f"  guarded path: {guarded}\n"
                "  fix: use the Edit / Write / MultiEdit tool on the file "
                "directly. Shell redirects, `tee`, `cp`, `mv`, `install`, "
                "`rsync`, `git checkout`, `git restore`, `git apply`, and any "
                "heredoc that writes to a guarded path are blocked. Symlink "
                "overwrites are also caught via realpath resolution."
            )

    # Layer C: kill/pkill against sidecar process names.
    for token in GUARDED_PROCESS_TOKENS:
        if re.search(r"\b(?:p?kill|killall)\b[^|;&]*" + re.escape(token), cmd):
            if _override_active():
                return 0, f"[hybrid-reasoner] override: process kill allowed via {ALLOW_OVERRIDE_ENV}=1 ({token})"
            return 2, (
                "[hybrid-reasoner] BLOCKED: shell kill targets the sidecar "
                "process.\n"
                f"  process token: {token}\n"
                "  fix: the sidecar (s2_core, mcp_reasoner, start-sidecar) must "
                "stay running for the gate to score edits. To restart it "
                "deliberately, ask the operator to run `rc restart` or "
                "`launchctl kickstart -k gui/$UID/...`."
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
            "  fix: use the Edit / Write / MultiEdit tools on the file directly. "
            "Those tools pipe through the SSM guard which scores the change. "
            "Shell-level writes (>, >>, tee, sed -i, python -c open(), "
            "pathlib.Path().write_text, cp / mv / install / rsync, "
            "git checkout / restore / stash apply, base64 -d | bash, openssl / xxd "
            "decoders piped to bash, python -c | python heredoc, or any "
            "redirect via stdin) are blocked regardless of whether the file is "
            "in src/ or /tmp.\n"
            "  escape hatch: if you genuinely need to write via shell and you "
            "are not trying to bypass the gate (e.g. generating a build "
            "artifact outside src/), use a path that does not match any "
            "SRC_EXTENSIONS entry and does not touch GUARDED_PATH_FRAGMENTS."
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

    cwd = payload.get("cwd")
    code, msg = screen_command(cmd, cwd if isinstance(cwd, str) else None)
    decision = "blocked" if code == 2 else "allowed"
    try:
        audit_log.append_event(audit_log.new_event(
            tool_name="Bash",
            decision=decision,
            command=cmd[:512],
            latency_ms=int((time.time() - started) * 1000),
            reason=msg.splitlines()[0] if msg else "",
            gate_id="rules",
        ))
    except Exception:  # noqa: BLE001
        pass
    _exit(code, msg)


if __name__ == "__main__":
    main()
