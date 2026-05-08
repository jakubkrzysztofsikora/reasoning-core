"""Tests for the Vibe stdin -> HookEnvelope adapter (Phase 4)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hooks.adapters import vibe  # noqa: E402


def test_post_agent_turn_envelope():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "/x.py"}, "session_id": "vibe-sess-1"}
    env = vibe.parse_payload("PostAgentTurn", json.dumps(payload))
    assert env.host == "vibe"
    assert env.session_id == "vibe-sess-1"
    assert env.event == "PostAgentTurn"


def test_garbage_returns_empty():
    env = vibe.parse_payload("PostAgentTurn", "}}}")
    assert env.tool_name is None
    assert env.host == "vibe"


def test_config_template_renders_to_valid_toml(tmp_path):
    """tomllib parse + spot-check fields."""
    import tomllib
    template = REPO / ".vibe" / "config.toml.template"
    out = template.read_text().replace("<RC_REPO>", str(tmp_path))
    parsed = tomllib.loads(out)
    servers = parsed["mcp_servers"]
    assert isinstance(servers, list) and len(servers) == 1
    assert servers[0]["name"] == "hybrid-reasoner"
    assert servers[0]["cwd"] == str(tmp_path)
    assert servers[0]["transport"] == "stdio"
