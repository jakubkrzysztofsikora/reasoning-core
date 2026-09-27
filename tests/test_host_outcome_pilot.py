from __future__ import annotations

import json
from pathlib import Path

from eval.host_outcome_pilot import run
from eval.host_outcome_pilot import post_write_feedback


def test_task_manifest_has_paired_policy_and_negative_cases():
    tasks = run._load_tasks(run.TASKS)
    assert {task["category"] for task in tasks} >= {
        "rule_engine", "plan_contract", "negative_control"
    }
    assert all(task["expected_final"] == "compliant" for task in tasks)


def test_gateway_environment_reads_only_anthropic_settings(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"env": {
        "ANTHROPIC_BASE_URL": "https://litellm.example",
        "ANTHROPIC_CUSTOM_HEADERS": "x-litellm-api-key: test",
        "UNRELATED_SECRET": "do-not-copy",
    }}), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert run._claude_gateway_env() == {
        "ANTHROPIC_BASE_URL": "https://litellm.example",
        "ANTHROPIC_CUSTOM_HEADERS": "x-litellm-api-key: test",
    }


def test_paired_settings_use_opposite_hook_phases():
    pre = run._settings("pre_write", "opencode/deepseek-v4.1-flash")["hooks"]
    post = run._settings("post_write", "opencode/deepseek-v4.1-flash")["hooks"]
    assert pre["PreToolUse"] and not pre["PostToolUse"]
    assert post["PostToolUse"] and not post["PreToolUse"]


def test_post_write_feedback_emits_actionable_context(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    task = run._load_tasks(run.TASKS)[0]
    run._write_fixture(project, task)
    target = project / task["seed_path"]
    target.write_text("import os\n" + task["seed"], encoding="utf-8")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({
        "cwd": str(project), "tool_input": {"file_path": str(target)},
    })))
    assert post_write_feedback.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert "policy feedback" in output["hookSpecificOutput"]["additionalContext"]
