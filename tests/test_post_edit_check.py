"""Tests for the PostToolUse fast deterministic check hook."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from src.hooks import post_edit_check as hook


@pytest.fixture(autouse=True)
def _clear_project_env(monkeypatch):
    monkeypatch.delenv("RC_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def _events(tmp_path: Path) -> list[dict]:
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    day_dir = tmp_path / "events" / day
    if not day_dir.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(day_dir.glob("*.jsonl")):
        rows.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return rows


def test_parse_check_passes_valid_python():
    result = hook.parse_check("foo.py", "value = 1\n")
    assert result is not None
    assert result["kind"] == "parse"
    assert result["status"] == "passed"


def test_parse_check_flags_python_syntax_error():
    result = hook.parse_check("foo.py", "def broken(:\n")
    assert result["status"] == "failed"
    assert "syntax" in result["first_error"].lower()


def test_parse_check_flags_invalid_json_and_skips_unknown_suffix():
    assert hook.parse_check("data.json", '{"a": 1}')["status"] == "passed"
    assert hook.parse_check("data.json", '{"a":')["status"] == "failed"
    assert hook.parse_check("notes.txt", "not source") is None


def test_yaml_parse_check_skips_oversized_documents():
    oversized = "key: " + "x" * (hook._MAX_YAML_BYTES + 32)

    assert hook.parse_check("config.yaml", oversized) is None
    assert hook.parse_check("config.yaml", "key: value\n")["status"] == "passed"


def test_yaml_parse_check_allows_anchor_definitions_without_aliases():
    anchored = "defaults: &defaults\n  adapter: postgres\n"

    assert hook.parse_check("config.yaml", anchored)["status"] == "passed"


def test_yaml_parse_check_skips_alias_expansion_documents():
    aliased = "defaults: &defaults\n  adapter: postgres\nprod: *defaults\n"

    assert hook.parse_check("config.yaml", aliased) is None


def test_changed_line_numbers_excludes_untouched_lines():
    before = "a = 1\nb = 2\nc = 3\n"
    after = "a = 1\nb = 22\nc = 3\nd = 4\n"

    assert hook.changed_line_numbers(before, after) == {2, 4}
    assert hook.changed_line_numbers("", after) == set()


def test_lint_check_skips_non_python():
    assert hook.lint_check("README.md") is None


def test_rules_check_without_rules_returns_none(tmp_path):
    assert hook.rules_check("foo.py", "value = 1\n", str(tmp_path)) is None


def test_feedback_summary_contains_actionable_fields():
    results = [{
        "kind": "lint",
        "status": "failed",
        "first_error": "foo.py:1:1: F401 unused import",
        "command": "ruff check",
    }]

    text = hook.format_feedback(
        results, failure_count=1, file_path="/repo/foo.py", project_dir="/repo"
    )

    assert "Verification: FAILED" in text
    assert "Check: lint" in text
    assert "File: foo.py" in text
    assert "First actionable error: foo.py:1:1: F401 unused import" in text
    assert "Repair: attempt 1 of 2" in text
    assert "Next action: repair, then rerun." in text


def test_feedback_abstains_when_repair_budget_is_exhausted():
    results = [{"kind": "parse", "status": "failed", "first_error": "line 1: bad"}]

    text = hook.format_feedback(
        results, failure_count=3, file_path="/repo/foo.py", project_dir="/repo"
    )

    assert "ask the user" in text
    assert "do not attempt further repairs" in text


def test_main_records_session_level_receipt_and_returns_advisory(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("RC_POST_EDIT_CHECKS", raising=False)

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-1",
        "tool_input": {"file_path": str(target)},
    })

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "Verification: FAILED" in payload["hookSpecificOutput"]["additionalContext"]
    assert "decision" not in payload

    receipts = [
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ]
    parse_receipts = [event for event in receipts if event["verification_kind"] == "parse"]
    assert parse_receipts
    receipt = parse_receipts[0]
    assert receipt["verification_status"] == "failed"
    assert receipt["deterministic"] is True
    assert receipt["association_type"] == "session_level"
    assert receipt["file_path_rel"] == "broken.py"
    assert receipt["session_id"] == "sess-1"


def test_main_uses_explicit_parent_when_host_supplies_one(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "ok.py"
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-2")

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-2",
        "decision_id": "decision-42",
        "tool_input": {"file_path": str(target)},
    })
    capsys.readouterr()

    receipts = [
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ]
    assert receipts
    assert receipts[0]["parent_decision_id"] == "decision-42"
    assert receipts[0]["association_type"] == "explicit_decision"


def test_main_is_silent_on_pass(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "ok.py"
    target.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-3")

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-3",
        "tool_input": {"file_path": str(target)},
    })

    assert capsys.readouterr().out == ""


def test_main_disabled_by_env(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-4")
    monkeypatch.setenv("RC_POST_EDIT_CHECKS", "0")

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-4",
        "tool_input": {"file_path": str(target)},
    })

    assert capsys.readouterr().out == ""
    assert _events(tmp_path) == []


def test_main_ignores_non_edit_tools(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-5")

    hook.main_with_payload({
        "tool_name": "Bash",
        "cwd": str(tmp_path),
        "session_id": "sess-5",
        "tool_input": {"command": "echo hi"},
    })

    assert capsys.readouterr().out == ""
    assert _events(tmp_path) == []


def test_manual_receipts_without_batch_id_do_not_consume_repair_budget(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    events_root = tmp_path / "events"
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    (events_root / day).mkdir(parents=True)
    manual = {
        "event_type": "verification_recorded",
        "decision_id": "manual-1",
        "session_id": "sess-7",
        "project_dir": str(project.resolve()),
        "file_path": str(target),
        "verification_status": "failed",
        "tool_name": "verification",
    }
    (events_root / day / "sess-7.jsonl").write_text(
        json.dumps(manual) + "\n" + json.dumps({**manual, "decision_id": "manual-2"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(events_root))
    monkeypatch.setenv("RC_SESSION_ID", "sess-7")

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-7",
        "tool_input": {"file_path": str(target)},
    })
    out = capsys.readouterr().out

    assert "Repair: attempt 1 of 2" in out


def test_duplicate_firing_for_identical_content_is_deduped(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-dedupe")
    payload = {
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-dedupe",
        "tool_input": {"file_path": str(target)},
    }

    hook.main_with_payload(payload)
    first_out = capsys.readouterr().out
    receipts_after_first = len([
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ])
    hook.main_with_payload(payload)
    second_out = capsys.readouterr().out

    assert "Verification: FAILED" in first_out
    assert second_out == ""
    assert len([
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ]) == receipts_after_first


def test_repair_budget_survives_payload_vs_audit_session_mismatch(tmp_path, monkeypatch, capsys):
    """Real Claude hooks use the payload UUID; the audit file is keyed by env."""
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "anon-audit-key")

    payload = {
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "claude-uuid-1234",
        "tool_input": {"file_path": str(target)},
    }
    outputs = []
    for attempt in range(3):
        target.write_text(f"def broken_{attempt}(:\n", encoding="utf-8")
        hook.main_with_payload(payload)
        outputs.append(capsys.readouterr().out)

    assert "Repair: attempt 1 of 2" in outputs[0]
    assert "Repair: attempt 2 of 2" in outputs[1]
    assert "ask the user" in outputs[2]
    # Receipts store the payload UUID in the row; the file key is the env key.
    receipts = [
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ]
    assert receipts
    assert receipts[0]["session_id"] == "claude-uuid-1234"


def test_project_dir_clamps_payload_cwd_outside_env_project(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "ok.py"
    target.write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-clamp")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

    hook.main_with_payload({
        "tool_name": "Edit",
        "cwd": str(outside),
        "session_id": "sess-clamp",
        "tool_input": {"file_path": str(target)},
    })
    capsys.readouterr()

    receipts = [
        event for event in _events(tmp_path)
        if event.get("event_type") == "verification_recorded"
    ]
    assert receipts
    assert receipts[0]["project_dir"] == str(project.resolve())
    assert receipts[0]["file_path_rel"] == "ok.py"


def test_main_abstains_after_repeated_failures(tmp_path, monkeypatch, capsys):
    project = tmp_path / "repo"
    project.mkdir()
    target = project / "broken.py"
    target.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(hook.audit_log, "_AUDIT_ROOT", str(tmp_path / "events"))
    monkeypatch.setenv("RC_SESSION_ID", "sess-6")

    payload = {
        "tool_name": "Edit",
        "cwd": str(project),
        "session_id": "sess-6",
        "tool_input": {"file_path": str(target)},
    }
    outputs = []
    for attempt in range(3):
        target.write_text(f"def broken_{attempt}(:\n", encoding="utf-8")
        hook.main_with_payload(payload)
        outputs.append(capsys.readouterr().out)

    assert "Repair: attempt 1 of 2" in outputs[0]
    assert "Repair: attempt 2 of 2" in outputs[1]
    assert "ask the user" in outputs[2]
