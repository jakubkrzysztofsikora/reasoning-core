"""Tests for the Antigravity CLI (agy) hook bridge."""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")
HOOKS_DIR = os.path.join(REPO_ROOT, "src", "hooks")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

from src.hooks import agy_bridge as bridge  # noqa: E402


def _agy_edit(tool: str = "replace_file_content", target: str = "/repo/src/foo.py") -> dict:
    args = {
        "TargetFile": target,
        "TargetContent": "old",
        "ReplacementContent": "new",
    }
    if tool == "write_to_file":
        args = {"TargetFile": target, "CodeContent": "hello", "Overwrite": True}
    return {
        "conversationId": "conv-1",
        "stepIdx": 3,
        "toolCall": {"name": tool, "args": args},
    }


def _agy_command(command: str = "pytest -q", cwd: str = "/repo") -> dict:
    return {
        "conversationId": "conv-1",
        "stepIdx": 4,
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": command, "Cwd": cwd},
        },
    }


def test_translate_replace_maps_to_claude_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_project_root", lambda path: "/repo")

    payload = bridge.translate(_agy_edit(), "pre_edit")

    assert payload["tool_name"] == "Edit"
    assert payload["tool_input"]["file_path"] == "/repo/src/foo.py"
    assert payload["tool_input"]["old_string"] == "old"
    assert payload["tool_input"]["new_string"] == "new"
    assert payload["session_id"] == "conv-1"
    assert payload["cwd"] == "/repo"


def test_translate_write_maps_to_claude_write(monkeypatch):
    monkeypatch.setattr(bridge, "_project_root", lambda path: "/repo")

    payload = bridge.translate(_agy_edit("write_to_file"), "post_edit")

    assert payload["tool_name"] == "Write"
    assert payload["tool_input"]["content"] == "hello"


def test_translate_command_maps_to_bash_with_exit_code(monkeypatch):
    payload = bridge.translate(
        {**_agy_command(), "error": "exit status 2"}, "post_command"
    )

    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"]["command"] == "pytest -q"
    assert payload["tool_response"]["exit_code"] == 2


def test_translate_command_success_is_zero(monkeypatch):
    payload = bridge.translate(_agy_command(), "post_command")

    assert payload["tool_response"]["exit_code"] == 0


def test_guard_decision_allow_and_deny():
    allow = bridge.guard_decision(0, "", "")
    deny = bridge.guard_decision(2, "", "blocked: guard file\nline two")

    assert allow == {"decision": "allow"}
    assert deny["decision"] == "deny"
    assert "guard file" in deny["reason"]


def test_guard_decision_fails_open_on_other_exit_codes():
    assert bridge.guard_decision(7, "weird", "boom") == {"decision": "allow"}


def test_pre_tool_uses_rc_hook_and_translates_block(tmp_path, monkeypatch, capsys):
    script = tmp_path / "fake_guard.py"
    script.write_text(
        "import sys\nsys.stdin.read()\nsys.stderr.write('nope')\nsys.exit(2)\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(bridge._RC_HOOKS, "pre_edit", str(script))
    monkeypatch.setattr(bridge, "_project_root", lambda path: "/repo")
    monkeypatch.setattr("sys.stdin", _Stdin(_agy_edit()))

    rc = bridge.main(["pre_edit"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["decision"] == "deny"
    assert "nope" in out["reason"]


def test_post_tool_records_and_replies_empty_object(tmp_path, monkeypatch, capsys):
    script = tmp_path / "fake_check.py"
    script.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    monkeypatch.setitem(bridge._RC_HOOKS, "post_edit", str(script))
    monkeypatch.setattr(bridge, "_project_root", lambda path: "/repo")
    monkeypatch.setattr("sys.stdin", _Stdin(_agy_edit()))

    rc = bridge.main(["post_edit"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out == {}


def test_pre_invocation_injects_feedback_for_recent_failed_episode(tmp_path, monkeypatch):
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    ts = now.isoformat()
    events = [
        {
            "event_type": "decision", "decision_id": "e1", "session_id": "conv-9",
            "project_dir": "/repo", "ts": ts, "file_path": "/repo/foo.py",
            "file_path_rel": "foo.py", "decision": "allowed", "tool_name": "Edit",
        },
        {
            "event_type": "verification_recorded", "decision_id": "c1", "session_id": "conv-9",
            "project_dir": "/repo", "ts": ts, "verification_kind": "parse",
            "verification_status": "failed", "parent_decision_id": "e1",
            "check_batch_id": "b1",
        },
    ]
    day = now.strftime("%Y-%m-%d")
    day_dir = tmp_path / day
    day_dir.mkdir(parents=True)
    (day_dir / "shared.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(bridge.audit_log, "_AUDIT_ROOT", str(tmp_path))

    message = bridge.feedback_message({"conversationId": "conv-9"})

    assert "Verification: FAILED" in message
    assert "foo.py" in message
    assert "Repair: attempt 1 of 2" in message


def test_pre_invocation_is_silent_without_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge.audit_log, "_AUDIT_ROOT", str(tmp_path))

    assert bridge.feedback_message({"conversationId": "conv-none"}) == ""


class _Stdin:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload)

    def read(self) -> str:
        return self._payload
