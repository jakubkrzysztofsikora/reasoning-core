"""Extension gate event handlers: bash screening, subagent audit, session-stop review."""

import hashlib
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def bridge():
    from src.hooks import omp_bridge
    return omp_bridge


def base_event(tmp_path, event, **extra):
    return {
        "event": event, "project_dir": str(tmp_path),
        "session_id": "gate-session", "agent_id": "agent-1",
        "parent_agent_id": None, **extra,
    }


@pytest.fixture
def gate_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("RC_", "S2_", "CLAUDE_", "OMP_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("RC_MODE", "copilot")
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "audit"))
    from src.hooks import audit_log
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", str(tmp_path / "audit"))
    return tmp_path / "audit"


def events(root):
    return [
        json.loads(line)
        for file in root.rglob("*.jsonl")
        for line in file.read_text().splitlines() if line.strip()
    ]


def audited(root, tool_name):
    return [row for row in events(root) if row.get("tool_name") == tool_name]


def test_bash_allow_audits_command_digest(tmp_path, gate_env):
    result = bridge()._evaluate_event(base_event(tmp_path, "bash", command="echo hi", cwd=None))
    assert result["decision"] == "allowed"
    assert result["reason"] == "bash_screen_passed"
    rows = audited(gate_env, "omp:bash")
    assert rows[-1]["decision"] == "allowed"
    assert rows[-1]["command_sha256"] == hashlib.sha256(b"echo hi").hexdigest()


def test_bash_blocked_command_audits_block(tmp_path, gate_env):
    result = bridge()._evaluate_event(
        base_event(tmp_path, "bash", command="git push --force origin main", cwd=str(tmp_path)),
    )
    assert result["decision"] == "blocked"
    rows = audited(gate_env, "omp:bash")
    assert rows[-1]["decision"] == "blocked"
    assert rows[-1]["decision_id"] == result["decision_id"]


def test_bash_screen_error_fails_closed(tmp_path, gate_env, monkeypatch):
    from src.hooks import pre_bash_guard
    monkeypatch.setattr(pre_bash_guard, "screen_command", lambda command, cwd: (1, "boom"))
    result = bridge()._evaluate_event(base_event(tmp_path, "bash", command="echo hi", cwd=None))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bash_screen_error"


def test_bash_audit_failure_fails_closed(tmp_path, gate_env, monkeypatch):
    monkeypatch.setattr(bridge(), "_audit", lambda *args, **kwargs: False)
    result = bridge()._evaluate_event(base_event(tmp_path, "bash", command="echo hi", cwd=None))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bridge_audit_unavailable"


def test_subagent_spawn_recorded(tmp_path, gate_env):
    result = bridge()._evaluate_event(base_event(tmp_path, "subagent", name="scout"))
    assert result["decision"] == "allowed"
    assert result["reason"] == "subagent_spawn_recorded"
    rows = audited(gate_env, "omp:subagent")
    assert rows[-1]["subagent_name"] == "scout"
    assert rows[-1]["decision_id"] == result["decision_id"]


def test_subagent_audit_failure_fails_closed(tmp_path, gate_env, monkeypatch):
    monkeypatch.setattr(bridge(), "_audit", lambda *args, **kwargs: False)
    result = bridge()._evaluate_event(base_event(tmp_path, "subagent", name="scout"))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bridge_audit_unavailable"


def test_advisor_cost_changed_recorded(tmp_path, gate_env):
    result = bridge()._evaluate_event(
        base_event(tmp_path, "advisor", kind="cost_changed", cost=0.42),
    )
    assert result["decision"] == "allowed"
    assert result["reason"] == "advisor_cost_changed"
    rows = audited(gate_env, "omp:advisor")
    assert rows[-1]["signal_source"] == "omp-advisor"
    assert rows[-1]["cost"] == 0.42
    assert rows[-1]["decision_id"] == result["decision_id"]


def test_advisor_yielded_recorded_without_cost(tmp_path, gate_env):
    result = bridge()._evaluate_event(
        base_event(tmp_path, "advisor", kind="yielded", cost=None),
    )
    assert result["decision"] == "allowed"
    assert result["reason"] == "advisor_yielded"
    rows = audited(gate_env, "omp:advisor")
    assert rows[-1]["cost"] is None


def test_advisor_rejects_invalid_kind(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "advisor", kind="spawned", cost=None))


def test_advisor_rejects_non_finite_cost(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "advisor", kind="cost_changed", cost="x"))
    with pytest.raises(ValueError):
        bridge()._evaluate_event(
            base_event(tmp_path, "advisor", kind="cost_changed", cost=float("inf")),
        )


@pytest.mark.parametrize("cost", [True, False])
def test_advisor_rejects_boolean_cost(tmp_path, gate_env, cost):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "advisor", kind="cost_changed", cost=cost))


def test_handle_event_advisor_roundtrip_subprocess(tmp_path, gate_env):
    result = bridge().handle_event(
        base_event(tmp_path, "advisor", kind="cost_changed", cost=1.5),
    )
    assert result["decision"] == "allowed"
    rows = audited(gate_env, "omp:advisor")
    assert rows[-1]["signal_source"] == "omp-advisor"
    assert rows[-1]["cost"] == 1.5


def test_session_stop_without_dup_oracle(tmp_path, gate_env):
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "allowed"
    assert result["reason"] == "session_stop"
    assert audited(gate_env, "omp:session_stop")[-1]["reason"] == "session_stop"


def test_session_stop_empty_ledger_is_advisory(tmp_path, gate_env, monkeypatch):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "allowed"
    assert result["reason"] == "dup_stop_advisory"


def test_session_stop_open_duplicate_blocks(tmp_path, gate_env, monkeypatch):
    from src.duplicate_detection.review import StopDecision
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(
        "src.duplicate_detection.review.stop_decision",
        lambda *args, **kwargs: StopDecision(
            block=True, message="open dup", extract=[{"id": "f1"}],
        ),
    )
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "blocked"
    assert result["reason"] == "open dup"
    rows = audited(gate_env, "omp:session_stop")
    assert rows[-1]["decision"] == "blocked"
    assert rows[-1]["extract_ids"] == ["f1"]


def test_session_stop_advise_mode_never_blocks(tmp_path, gate_env, monkeypatch):
    from src.duplicate_detection.review import StopDecision
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setenv("RC_MODE", "advise")
    monkeypatch.setattr(
        "src.duplicate_detection.review.stop_decision",
        lambda *args, **kwargs: StopDecision(
            block=True, message="open dup", extract=[{"id": "f1"}],
        ),
    )
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "allowed"
    assert result["reason"] == "dup_stop_advisory"


def test_session_stop_dup_error_fails_open(tmp_path, gate_env, monkeypatch):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")

    def broken(*args, **kwargs):
        raise RuntimeError("reviewer unavailable")

    monkeypatch.setattr("src.duplicate_detection.review.stop_decision", broken)
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "allowed"
    assert result["reason"] == "dup_stop_fail_open"


def test_session_stop_audit_failure_fails_closed(tmp_path, gate_env, monkeypatch):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(bridge(), "_audit", lambda *args, **kwargs: False)
    result = bridge()._evaluate_event(base_event(tmp_path, "session_stop"))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bridge_audit_unavailable"


def test_bash_rejects_missing_command(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "bash", cwd=None))


def test_bash_rejects_extra_field(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "bash", command="echo hi", cwd=None, junk=1))


def test_bash_rejects_invalid_cwd(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "bash", command="echo hi", cwd=5))


def test_subagent_rejects_blank_name(tmp_path, gate_env):
    with pytest.raises(ValueError):
        bridge()._evaluate_event(base_event(tmp_path, "subagent", name="   "))


def test_handle_event_bash_roundtrip_subprocess(tmp_path, gate_env):
    result = bridge().handle_event(base_event(tmp_path, "bash", command="echo hi", cwd=None))
    assert result["decision"] == "allowed"
    assert result["reason"] == "bash_screen_passed"
    assert audited(gate_env, "omp:bash")[-1]["command_sha256"] == hashlib.sha256(b"echo hi").hexdigest()


def test_handle_event_invalid_bash_blocked(tmp_path, gate_env):
    result = bridge().handle_event(base_event(tmp_path, "bash", cwd=None))
    assert result["decision"] == "blocked"
    assert result["reason"] == "invalid_event"


def test_handle_event_subagent_roundtrip_subprocess(tmp_path, gate_env):
    result = bridge().handle_event(base_event(tmp_path, "subagent", name="scout"))
    assert result["decision"] == "allowed"
    assert audited(gate_env, "omp:subagent")[-1]["subagent_name"] == "scout"
