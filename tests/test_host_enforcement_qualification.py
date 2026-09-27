from __future__ import annotations

from eval.host_enforcement_qualification import run


def test_qualification_settings_cover_supported_file_tools(tmp_path):
    settings = run._settings("chatgpt/gpt-5.6-terra", tmp_path / "trace.jsonl")
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Edit|Write|MultiEdit"
    assert len(entry["hooks"]) == 2
    assert "trace_hook.py" in entry["hooks"][0]["command"]
    assert "pre_edit_guard.py" in entry["hooks"][1]["command"]


def test_case_prompts_force_each_qualified_tool():
    assert "Edit tool" in run._case_prompt("Edit", True)
    assert "Write tool" in run._case_prompt("Write", True)
    assert "MultiEdit tool" in run._case_prompt("MultiEdit", True)
    assert "src/config.py" in run._case_prompt("Write", True)
    assert "src/service.py" in run._case_prompt("Edit", False)


def test_unavailable_channel_is_excluded_not_a_qualification_failure():
    rows = [
        {"qualification": "pass"},
        {"qualification": "tool_unavailable"},
    ]
    failures = [row for row in rows if row["qualification"] in run.ENFORCEMENT_FAILURES]
    inconclusive = [row for row in rows if row["qualification"] in run.RETRYABLE]
    qualified = not failures and not inconclusive and all(row["qualification"] in {"pass", "tool_unavailable"} for row in rows)
    assert qualified is True
