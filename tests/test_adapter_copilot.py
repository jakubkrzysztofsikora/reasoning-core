"""Tests for the Copilot stdin -> HookEnvelope adapter (Phase 3)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hooks.adapters import copilot  # noqa: E402


def test_canonical_tool_mapping():
    payload = {"tool_name": "write", "tool_input": {"file_path": "/x.py"}}
    env = copilot.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Write"
    assert env.host == "copilot"


def test_str_replace_editor_to_edit():
    payload = {"tool_name": "str_replace_editor", "tool_input": {"path": "/x.py"}}
    env = copilot.parse_payload("PreToolUse", json.dumps(payload))
    assert env.tool_name == "Edit"
    assert env.file_path == "/x.py"


def test_garbage_returns_empty():
    env = copilot.parse_payload("PreToolUse", "{{")
    assert env.tool_name is None
    assert env.host == "copilot"


def test_emit_block_decision_format(capsys):
    copilot.emit_block_decision("blocked: regression detected")
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["permissionDecision"] == "deny"
    assert "blocked" in parsed["permissionDecisionReason"]


def test_mcp_template_renders_to_valid_json(tmp_path):
    template = REPO / ".copilot" / "mcp-config.template.json"
    out = template.read_text().replace("<RC_REPO>", str(tmp_path))
    parsed = json.loads(out)
    server = parsed["mcpServers"]["hybrid-reasoner"]
    assert server["cwd"] == str(tmp_path)
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "src.mcp_reasoner"]
