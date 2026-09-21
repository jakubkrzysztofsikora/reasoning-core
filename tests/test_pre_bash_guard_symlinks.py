"""Regression: symlink-overwrite vector must stay closed.

RC-SEC-05 hostile re-audit finding:
    pre_bash_guard.py previously matched only substrings in the raw command.
    An attacker could create a symlink /tmp/payload.tmp -> src/hooks/pre_bash_guard.py
    and overwrite through the symlink with `echo x > /tmp/payload.tmp`,
    bypassing the guard because /tmp/payload.tmp has no SRC_EXTENSIONS entry
    and the substring src/hooks/pre_bash_guard.py is not in the literal cmd.

Closure: _resolve_symlink_to_guarded() inspects each redirect target, expands
~ and resolves os.path.realpath(), and treats a realpath that contains any
GUARDED_PATH_FRAGMENT as a guarded write. The test below simulates the
end-to-end attack by creating a real symlink and running the actual hook
binary.
"""
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


def _run_hook(cmd: str, env_extra: dict[str, str] | None = None):
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
# Unit-level: _resolve_symlink_to_guarded() detects symlink → guarded target
# ---------------------------------------------------------------------------

def test_resolve_symlink_to_guarded_finds_target(tmp_path):
    mod = _load_module()
    guarded = tmp_path / "src" / "hooks" / "pre_bash_guard.py"
    guarded.parent.mkdir(parents=True)
    guarded.write_text("# stub\n")
    link = tmp_path / "payload.tmp"
    os.symlink(str(guarded), str(link))
    # `echo x > payload.tmp` would not trigger the substring layer, but the
    # resolved-target scanner should detect that the destination realpath
    # contains "src/hooks/pre_bash_guard.py".
    cmd = f"echo x > {link}"
    hit = mod._resolve_symlink_to_guarded(cmd)
    assert hit is not None, f"expected guarded fragment hit for {cmd!r}"
    assert "pre_bash_guard.py" in hit


def test_resolve_symlink_to_guarded_no_match_for_benign(tmp_path):
    mod = _load_module()
    benign = tmp_path / "logs" / "app.log"
    benign.parent.mkdir(parents=True)
    benign.write_text("benign\n")
    cmd = f"echo x > {benign}"
    hit = mod._resolve_symlink_to_guarded(cmd)
    assert hit is None


def test_resolve_symlink_to_guarded_no_match_for_missing(tmp_path):
    mod = _load_module()
    cmd = f"echo x > {tmp_path / 'does-not-exist.tmp'}"
    hit = mod._resolve_symlink_to_guarded(cmd)
    # Missing path returns None; the regex layer still owns that surface.
    assert hit is None


# ---------------------------------------------------------------------------
# End-to-end: real hook binary blocks the symlink overwrite
# ---------------------------------------------------------------------------

def test_symlink_overwrite_through_tmp_blocks(tmp_path, monkeypatch):
    """The full hostile chain: ln -s src/hooks/pre_bash_guard.py payload.tmp,
    then `echo x > payload.tmp` must be blocked with exit 2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_dir = repo / "src" / "hooks"
    hooks_dir.mkdir(parents=True)
    guarded = hooks_dir / "pre_bash_guard.py"
    guarded.write_text("# original\n")

    # Run the link command first -- the link creation itself doesn't touch a
    # guarded file, so it should pass.
    link = repo / "payload.tmp"
    code, _ = _run_hook(f"ln -s {guarded} {link}")
    assert code == 0, f"link creation unexpectedly blocked (exit {code})"

    # Now create the actual symlink (the hook only inspects intent, not
    # filesystem state; we materialize it so _resolve_symlink_to_guarded
    # has a realpath to resolve).
    os.symlink(str(guarded), str(link))

    # Run the attack through the hook binary. We point cwd at the repo by
    # chdir-ing the subprocess via env CURRENT_DIR isn't honored, so use the
    # absolute path of the link in the cmd.
    code, stderr = _run_hook(f"echo evil > {link}")
    assert code == 2, (
        f"expected symlink overwrite to be blocked (exit 2), got {code}; "
        f"stderr={stderr!r}"
    )
    assert "BLOCKED" in stderr
    # And the guarded file on disk must be untouched.
    assert guarded.read_text() == "# original\n"


def test_symlink_in_source_blocks(tmp_path):
    """A symlink in the source position of a cp/mv chain that resolves to
    a guarded file should still be caught. The attacker chains:
      ln -s src/hooks/pre_bash_guard.py /tmp/innocent.py
      cp /tmp/innocent.py /tmp/evil.py
    The cp is allowed (no guarded path on the surface) but the destination
    already exists; a subsequent `echo x > /tmp/innocent.py` would trip
    the symlink resolver. This test exercises the redirect target that
    the symlink resolver is designed to catch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_dir = repo / "src" / "hooks"
    hooks_dir.mkdir(parents=True)
    guarded = hooks_dir / "pre_bash_guard.py"
    guarded.write_text("# original\n")
    link = tmp_path / "innocent.py"
    os.symlink(str(guarded), str(link))
    code, stderr = _run_hook(f"echo evil > {link}")
    assert code == 2, (
        f"expected redirect into symlink-to-guarded to block (got exit {code}); "
        f"stderr={stderr!r}"
    )
    assert "BLOCKED" in stderr
    assert "symlink" in stderr.lower()
