"""Historical acknowledgements must never authorize later file versions."""
import argparse
import importlib
import subprocess
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize("filename", ["existing.py", "file with spaces.py"])
def test_acknowledgement_is_session_and_content_bound(tmp_path, monkeypatch, filename):
    from src import rc_cli
    importlib.reload(rc_cli)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    target = repo / filename
    target.write_text("before\n")
    audit = tmp_path / "audit"
    monkeypatch.setenv("RC_RUN_DIR", str(repo))
    monkeypatch.setenv("RC_AUDIT_ROOT", str(audit))
    monkeypatch.setattr(rc_cli.audit_log, "_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("RC_SESSION_ID", "test-session")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    args = argparse.Namespace(json=True, acknowledge_current=True,
                              reason="User requested historical reconciliation")
    assert rc_cli.cmd_reconcile(args) == 0
    assert rc_cli._reconcile_missing_gate_events(str(repo), str(audit), "test-session") == []
    assert rc_cli._reconcile_missing_gate_events(str(repo), str(audit), "another-session") == [filename]
    other = tmp_path / "other-repo"
    other.mkdir()
    subprocess.run(["git", "init", str(other)], check=True, capture_output=True)
    (other / filename).write_text("before\n")
    assert rc_cli._reconcile_missing_gate_events(str(other), str(audit), "test-session") == [filename]
    target.chmod(0o755)
    assert rc_cli._reconcile_missing_gate_events(str(repo), str(audit), "test-session") == [filename]
    target.write_text("after\n")
    assert rc_cli._reconcile_missing_gate_events(str(repo), str(audit), "test-session") == [filename]


def test_acknowledgement_requires_reason(tmp_path, monkeypatch):
    from src import rc_cli
    monkeypatch.setenv("RC_RUN_DIR", str(tmp_path))
    args = argparse.Namespace(json=True, acknowledge_current=True, reason="")
    assert rc_cli.cmd_reconcile(args) == 2
