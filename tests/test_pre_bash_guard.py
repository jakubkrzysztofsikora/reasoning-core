"""Tests for src/hooks/pre_bash_guard.py — the Bash escape-route guard."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "src" / "hooks" / "pre_bash_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pre_bash_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run_hook(cmd: str, env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    env = {**os.environ}
    env.pop("RC_ALLOW_GUARD_EDIT", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stderr


# ---------------------------------------------------------------------------
# Hard-deny patterns (Layer A)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "pkill -f s2_core",
        "pkill -9 mcp_reasoner",
        "killall start-sidecar",
        "echo bad > .claude/settings.json",
        "cat > .claude/settings.json <<EOF\n{}\nEOF",
        "tee .claude/settings.json < /dev/null",
        "rm .claude/settings.json",
        "rm -rf src/hooks/",
        "chmod -x src/hooks/pre_edit_guard.py",
        "unset S2_FAIL_CLOSED",
        "unset RC_ALLOW_GUARD_EDIT",
    ],
)
def test_hard_deny_blocks(cmd: str) -> None:
    code, stderr = _run_hook(cmd)
    assert code == 2, f"expected block, got {code}; stderr={stderr!r}"
    assert "BLOCKED" in stderr


def test_hard_deny_override_lets_through() -> None:
    code, _ = _run_hook("pkill -f s2_core", env_extra={"RC_ALLOW_GUARD_EDIT": "1"})
    assert code == 0


# ---------------------------------------------------------------------------
# Source-write patterns (Layer D)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "echo 'import os' > foo.py",
        "echo 'export X=1' >> deploy.sh",
        "tee bar.ts <<EOF\nconst x = 1;\nEOF",
        "sed -i 's/foo/bar/g' main.py",
        'python3 -c "open(\'x.py\',\'w\').write(\'x=1\')"',
        'node -e "require(\'fs\').writeFileSync(\'a.ts\',\'x\')"',
        "cat <<EOF > new.cs\nclass K{}\nEOF",
    ],
)
def test_source_write_blocks(cmd: str) -> None:
    code, stderr = _run_hook(cmd)
    assert code == 2, f"expected block, got {code}; stderr={stderr!r}"
    assert "BLOCKED" in stderr


# ---------------------------------------------------------------------------
# Allow-list — common dev workflow stays unblocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la src/",
        "cat README.md",
        "grep -rn TODO src",
        "git status",
        "git log --oneline -5",
        "git diff HEAD~1",
        "pytest -q tests/",
        "python3 -m pytest -q",
        "ruff check src",
        "npm install",
        "node --version",
        "uv sync",
        "make build",
        "curl -fsS https://example.com",
        "ls && pwd",
        "head -5 docs/PLAN.md",
        "find . -name '*.py' -not -path '*/.venv/*'",
    ],
)
def test_safe_commands_allowed(cmd: str) -> None:
    code, stderr = _run_hook(cmd)
    assert code == 0, f"expected allow, got {code}; stderr={stderr!r}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_command_allowed() -> None:
    code, _ = _run_hook("")
    assert code == 0


def test_non_bash_tool_allowed() -> None:
    payload = '{"tool_name": "Edit", "tool_input": {"command": "rm -rf /"}}'
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0


def test_malformed_stdin_allowed() -> None:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# screen_command unit-level
# ---------------------------------------------------------------------------

def test_screen_command_unit() -> None:
    mod = _load_module()
    assert mod.screen_command("ls")[0] == 0
    assert mod.screen_command("pkill -f s2_core")[0] == 2
    assert mod.screen_command("echo x > main.py")[0] == 2
    assert mod.screen_command("git status")[0] == 0
