"""Phase 1a: tests for the Claude stdin -> HookEnvelope adapter."""
import json
import sys
from pathlib import Path
from types import MappingProxyType

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hooks._envelope import HookEnvelope  # noqa: E402
from src.hooks.adapters import claude  # noqa: E402


def test_parse_well_formed_edit_payload():
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/tmp/foo.py",
            "old_string": "x",
            "new_string": "y",
        },
        "session_id": "sess-abc",
        "cwd": "/tmp",
    }
    env = claude.parse_payload("PreToolUse", json.dumps(payload))
    assert isinstance(env, HookEnvelope)
    assert env.event == "PreToolUse"
    assert env.host == "claude"
    assert env.tool_name == "Edit"
    assert env.tool_input["old_string"] == "x"
    assert env.file_path == "/tmp/foo.py"
    assert env.session_id == "sess-abc"
    assert env.cwd == "/tmp"
    # raw escape hatch
    assert env.raw["tool_name"] == "Edit"


def test_parse_garbage_returns_empty_envelope_no_raise():
    env = claude.parse_payload("PreToolUse", "not json {{{")
    assert env.tool_name is None
    assert env.host == "claude"
    assert env.event == "PreToolUse"
    assert dict(env.raw) == {}


def test_parse_empty_returns_empty_envelope():
    env = claude.parse_payload("PreToolUse", "")
    assert env.tool_name is None
    assert dict(env.raw) == {}


def test_parse_non_dict_returns_empty_envelope():
    env = claude.parse_payload("PreToolUse", "[1, 2, 3]")
    assert env.tool_name is None
    assert dict(env.raw) == {}


def test_envelope_is_immutable():
    env = claude.parse_payload("PreToolUse", json.dumps({"tool_name": "Write"}))
    # frozen dataclass: cannot reassign field
    import dataclasses
    try:
        env.tool_name = "Edit"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("expected FrozenInstanceError")
    # raw is a MappingProxyType, cannot mutate
    try:
        env.raw["x"] = 1  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("expected raw to be immutable")


def test_path_field_alias():
    """Some Edit invocations use 'path' instead of 'file_path'."""
    payload = {"tool_name": "Edit", "tool_input": {"path": "/tmp/foo.py"}}
    env = claude.parse_payload("PreToolUse", json.dumps(payload))
    assert env.file_path == "/tmp/foo.py"


def test_missing_tool_input_does_not_crash():
    payload = {"tool_name": "Edit"}  # no tool_input
    env = claude.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Edit"
    assert dict(env.tool_input) == {}
    assert env.file_path is None


def test_session_start_envelope_has_no_tool():
    payload = {"transcript_path": "/tmp/t.jsonl", "cwd": "/tmp"}
    env = claude.parse_payload("SessionStart", json.dumps(payload))
    assert env.event == "SessionStart"
    assert env.tool_name is None
    assert env.transcript_path == "/tmp/t.jsonl"
