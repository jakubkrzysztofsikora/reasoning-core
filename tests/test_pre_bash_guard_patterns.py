"""Regression tests for the audit-hostile/2026-09-19-fixes shell-guard extensions.

Each test case is a specific bypass the 2026-09-19 hostile audit surfaced.
"""
from __future__ import annotations

import pytest

from src.hooks import pre_bash_guard as pb


# Each tuple: (cmd, should_block, label)
AUDIT_BYPASSES = [
    # 1. mv the hook script out of place
    ("mv src/hooks/pre_bash_guard.py /tmp/pre_bash_guard.py", True, "mv hook file"),
    # 2. cp payload over a hook file
    ("cp /tmp/payload.py src/hooks/pre_bash_guard.py", True, "cp payload"),
    # 3. install(1) over a hook file
    ("install -m 644 /tmp/payload.py src/hooks/pre_bash_guard.py", True, "install payload"),
    # 4. rsync a hook file
    ("rsync -a /tmp/payload.py src/hooks/pre_bash_guard.py", True, "rsync hook"),
    # 5. python3 -c with pathlib.Path().write_text()
    (
        "python3 -c \"import pathlib; pathlib.Path('src/hooks/pre_bash_guard.py').write_text('')\"",
        True,
        "pathlib write_text",
    ),
    # 6. base64-decoded payload piped to bash
    ("echo cm0gLXJmIHNyYy9ob29rcw== | base64 -d | bash", True, "base64 pipe bash"),
    # 7. base64 inside $() subshell
    ("$(echo cm0gLXJmIHNyYy9ob29rcw== | base64 -d)", True, "base64 subshell"),
    # 8. git checkout HEAD~1 -- <hook file>
    ("git checkout HEAD~1 -- src/hooks/pre_bash_guard.py", True, "git checkout hook"),
    # 9. git apply (any form, blocks outright)
    ("git apply malicious.patch", True, "git apply"),
    # 10. git restore (any form)
    ("git restore src/hooks/pre_bash_guard.py", True, "git restore"),
    # 11. git stash apply
    ("git stash apply", True, "git stash apply"),
    # 12. bare-SHA git checkout (no paths)
    ("git checkout 8a3f4b2", True, "git checkout SHA"),
    # 13. echo redirect to a settings.json (already covered, regression)
    ('echo "{}" > .claude/./settings.json', True, "settings.json redirect"),
]


# Benign commands that must still pass.
BENIGN = [
    "git status",
    "git log --oneline",
    "git diff HEAD~1",
    "git show HEAD",
    "git branch -a",
    "ls -la src/hooks/",
    "pytest -q tests/test_hook_block.py",
    "ruff check src/s2_core.py",
    "python -m pip install requests",
    "npm install",
    "cargo build --release",
    "make -j4 test",
]


@pytest.mark.parametrize("cmd,should_block,label", AUDIT_BYPASSES)
def test_audit_bypass_blocked(cmd, should_block, label):
    code, _msg = pb.screen_command(cmd)
    assert (code == 2) == should_block, (
        f"{label}: cmd={cmd!r} expected_block={should_block} got_block={code==2}"
    )


@pytest.mark.parametrize("cmd", BENIGN)
def test_benign_command_allowed(cmd):
    code, _msg = pb.screen_command(cmd)
    assert code == 0, f"benign cmd wrongly blocked: {cmd!r}"
