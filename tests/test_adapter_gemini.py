"""Tests for the Gemini stdin -> HookEnvelope adapter (Phase 2)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hooks.adapters import gemini  # noqa: E402


def test_canonical_tool_name_mapping():
    payload = {"tool_name": "write_file", "tool_input": {"file_path": "/tmp/a.py", "content": "x"}}
    env = gemini.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Write"
    assert env.host == "gemini"
    assert env.file_path == "/tmp/a.py"


def test_edit_file_normalises_to_edit():
    payload = {"tool_name": "edit_file", "tool_input": {"file_path": "/tmp/a.py", "old_string": "x", "new_string": "y"}}
    env = gemini.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Edit"


def test_run_shell_command_normalises_to_bash():
    payload = {"tool_name": "run_shell_command", "tool_input": {"command": "ls"}}
    env = gemini.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Bash"


def test_unknown_tool_name_passes_through():
    payload = {"tool_name": "exotic_tool", "tool_input": {}}
    env = gemini.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "exotic_tool"


def test_garbage_stdin_returns_empty_envelope():
    env = gemini.parse_payload("PreToolUse", "{{not json")
    assert env.tool_name is None
    assert env.host == "gemini"


def test_str_replace_editor_alias():
    payload = {"tool_name": "str_replace_editor", "tool_input": {"path": "/x.py"}}
    env = gemini.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Edit"
    assert env.file_path == "/x.py"


def test_settings_template_renders_to_valid_json(tmp_path):
    """Substituting <RC_REPO> in the template must yield valid JSON with the
    canonical Gemini event names (PreToolUse, PostToolUse, SessionStart, etc.)."""
    template = REPO / ".gemini" / "settings.json.template"
    out = template.read_text().replace("<RC_REPO>", str(tmp_path))
    parsed = json.loads(out)
    hooks = parsed["hooks"]
    for required in ("PreToolUse", "PostToolUse", "SessionStart"):
        assert required in hooks, f"missing event: {required}"
    # MCP cwd MUST be set (else ModuleNotFoundError from $HOME)
    assert parsed["mcpServers"]["hybrid-reasoner"]["cwd"] == str(tmp_path)
