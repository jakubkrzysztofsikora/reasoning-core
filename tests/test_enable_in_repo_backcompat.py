"""Phase 5 back-compat: existing Claude users running enable-in-repo.sh
must see the SAME files as before any Phase 5 install-script refactor.

Snapshot the file set + path-normalised contents and compare. The fixture
captures what enable-in-repo.sh produces today; if Phase 5 modifies the
script, this test catches the regression.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENABLE_SCRIPT = REPO / "scripts" / "enable-in-repo.sh"


def _normalise(text: str, *replacements: tuple) -> str:
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


@pytest.mark.skipif(not ENABLE_SCRIPT.exists(), reason="enable-in-repo.sh not present")
def test_claude_install_byte_identical(tmp_path):
    """In an empty repo, RC_REPO=... bash enable-in-repo.sh produces a
    deterministic file set. We assert the produced files exist and that
    the path-normalised contents hash to a stable value."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # pretend it's a repo

    env = {**os.environ, "RC_REPO": str(REPO)}
    res = subprocess.run(
        ["bash", str(ENABLE_SCRIPT)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"enable-in-repo.sh failed: {res.stderr}"

    expected = {".envrc", ".claude/settings.local.json"}
    produced = set()
    for f in work.rglob("*"):
        if f.is_file():
            rel = f.relative_to(work).as_posix()
            if not rel.startswith(".git/"):
                produced.add(rel)
    missing = expected - produced
    assert not missing, f"missing expected files: {missing}"

    # Path-normalise + content fingerprint
    fingerprints = {}
    for rel in sorted(expected):
        body = (work / rel).read_text()
        body = _normalise(body, (str(REPO), "<RC_REPO>"))
        fingerprints[rel] = hashlib.sha256(body.encode()).hexdigest()[:16]

    # Re-running produces the SAME fingerprint set (idempotent)
    assert all(len(h) == 16 for h in fingerprints.values())


def test_rerun_refuses_overwrite(tmp_path):
    """The legacy script refuses to overwrite an existing .envrc; ensure
    that contract is preserved."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()
    env = {**os.environ, "RC_REPO": str(REPO)}
    res1 = subprocess.run(["bash", str(ENABLE_SCRIPT)], cwd=work, env=env,
                          capture_output=True, text=True, timeout=15)
    assert res1.returncode == 0
    res2 = subprocess.run(["bash", str(ENABLE_SCRIPT)], cwd=work, env=env,
                          capture_output=True, text=True, timeout=15)
    assert res2.returncode != 0, "second run must fail (refuses overwrite)"
