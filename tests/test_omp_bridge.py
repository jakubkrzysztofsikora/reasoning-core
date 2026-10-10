"""OMP bridge containment and protocol checks using a real local score server."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def bridge():
    assert importlib.util.find_spec("src.hooks.omp_bridge") is not None, (
        "OMP guard bridge is not implemented"
    )
    from src.hooks import omp_bridge
    return omp_bridge


def prepared(project, *, session="omp-session", agent="agent-1"):
    source = project / "one.py"
    source.write_text("x = 1\n")
    return {
        "event": "prepare",
        "batch": {
            "protocol_version": 1, "project_dir": str(project),
            "session_id": session, "agent_id": agent, "parent_agent_id": None,
            "tool_call_id": "tool-1", "final_input_sha256": "a" * 64,
            "candidates": [{
                "path": str(source), "operation": "write",
                "before_src": "x = 1\n", "after_src": "x = 2\n",
                "expected_before_sha256": hashlib.sha256(b"x = 1\n").hexdigest(),
            }],
        },
    }


@pytest.fixture
def score_server():
    state = {"status": 200, "report": {
        "regression_detected": False, "human_summary": "clean",
        "architectural_impact_score": 1.0,
    }, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            state["requests"].append({
                "body": json.loads(self.rfile.read(size)),
                "authorization": self.headers.get("Authorization"),
            })
            body = json.dumps(state["report"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch, score_server):
    for key in list(os.environ):
        if key.startswith(("RC_", "S2_", "CLAUDE_", "OMP_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("S2_URL", score_server["url"])
    monkeypatch.setenv("RC_ENFORCEMENT_TOKEN", "test-bridge-token")
    monkeypatch.setenv("RC_MODE", "copilot")
    monkeypatch.setenv("S2_FAIL_CLOSED", "1")
    monkeypatch.setenv("RC_RULE_ENGINE", "1")
    monkeypatch.setenv("RC_ORACLE_BLOCK", "1")
    monkeypatch.setenv("RC_ORACLE_T2", "0")
    monkeypatch.setenv("RC_AUDIT_ROOT", str(tmp_path / "audit"))
    return tmp_path / "audit"


def events(root):
    return [
        json.loads(line)
        for file in root.rglob("*.jsonl")
        for line in file.read_text().splitlines() if line.strip()
    ]


def test_allowed_prepare_uses_shared_auth_and_never_writes_candidate(
    tmp_path, isolated_env, score_server,
):
    event = prepared(tmp_path)
    result = bridge().handle_event(event)
    assert result["decision"] == "allowed"
    assert (tmp_path / "one.py").read_text() == "x = 1\n"
    request = score_server["requests"][0]
    assert request["authorization"] == "Bearer test-bridge-token"
    assert request["body"]["host"] == "omp"
    assert request["body"]["session_id"] == "omp-session"
    rows = [row for row in events(isolated_env) if row.get("signal_source") == "omp_bridge"]
    assert rows[-1]["decision_id"] == result["decision_id"]
    assert rows[-1]["host"] == "omp"
    assert rows[-1]["project_dir"] == str(tmp_path)
    assert rows[-1]["agent_id"] == "agent-1"
    assert "test-bridge-token" not in json.dumps(result)


def test_last_forbidden_import_denies_entire_batch_before_scoring(
    tmp_path, isolated_env, score_server,
):
    config = tmp_path / ".reasoning-core"
    config.mkdir()
    (config / "rules.yaml").write_text(
        "corpus_version: v1\nrules:\n  - id: no-os\n    type: forbid_import\n"
        "    severity: deny\n    language: python\n    target: os\n"
    )
    event = prepared(tmp_path)
    (tmp_path / "two.py").write_text("x = 1\n")
    final = dict(event["batch"]["candidates"][0])
    final.update(path=str(tmp_path / "two.py"), after_src="import os\n")
    event["batch"]["candidates"].append(final)
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert "rule_engine:no-os:" in result["reason"]
    assert (tmp_path / "one.py").read_text() == "x = 1\n"
    assert (tmp_path / "two.py").read_text() == "x = 1\n"
    assert not score_server["requests"]


def test_neural_regression_is_advisory_even_with_legacy_flag_disabled(
    tmp_path, isolated_env, score_server, monkeypatch,
):
    score_server["report"]["regression_detected"] = True
    monkeypatch.setenv("RC_NEURAL_CORROBORATED", "0")
    result = bridge().handle_event(prepared(tmp_path))
    assert result["decision"] == "allowed"
    assert any(row.get("reason") == "neural_uncorroborated_advisory"
               for row in events(isolated_env))


def test_parse_regression_blocks_before_neural_score(tmp_path, isolated_env, score_server):
    event = prepared(tmp_path)
    event["batch"]["candidates"][0]["after_src"] = "def broken(:\n"
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert "oracle" in result["reason"]
    assert not score_server["requests"]


@pytest.mark.parametrize("fail_closed,expected", [("1", "blocked"), ("0", "allowed")])
def test_auth_outage_honors_configured_failure_policy(
    tmp_path, isolated_env, score_server, monkeypatch, fail_closed, expected,
):
    score_server["status"] = 401
    monkeypatch.setenv("S2_FAIL_CLOSED", fail_closed)
    result = bridge().handle_event(prepared(tmp_path))
    assert result["decision"] == expected
    assert "auth" in result["reason"]


def test_stale_candidate_blocks_without_scoring(tmp_path, isolated_env, score_server):
    event = prepared(tmp_path)
    (tmp_path / "one.py").write_text("different\n")
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert "stale" in result["reason"]
    assert not score_server["requests"]


def test_context_isolated_for_concurrent_child_sessions(
    tmp_path, isolated_env, score_server,
):
    inputs = []
    for number in range(2):
        project = tmp_path / f"child-{number}"
        project.mkdir()
        inputs.append(prepared(project, session=f"child-{number}", agent=f"agent-{number}"))
    original = dict(os.environ)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(bridge().handle_event, inputs))
    assert all(result["decision"] == "allowed" for result in results)
    assert dict(os.environ) == original
    observed = {
        row["session_id"]: (row["agent_id"], row["project_dir"])
        for row in events(isolated_env) if row.get("signal_source") == "omp_bridge"
    }
    assert observed == {
        f"child-{number}": (f"agent-{number}", str(tmp_path / f"child-{number}"))
        for number in range(2)
    }


def test_guard_path_protection_survives_advise_mode(tmp_path, isolated_env, monkeypatch):
    monkeypatch.setenv("RC_MODE", "advise")
    event = prepared(tmp_path)
    owned = tmp_path / ".omp" / "extensions" / "reasoning_core_gate.ts"
    owned.parent.mkdir(parents=True)
    owned.write_text("x = 1\n")
    event["batch"]["candidates"][0]["path"] = str(owned)
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert "guard" in result["reason"]


def test_cli_emits_one_json_response_for_malformed_input(tmp_path, isolated_env):
    bridge()
    process = subprocess.run(
        [sys.executable, "-m", "src.hooks.omp_bridge"],
        input="{bad", text=True, capture_output=True, cwd=REPO, timeout=10,
    )
    assert process.returncode != 0
    response = json.loads(process.stdout)
    assert response["decision"] == "blocked"
    assert response["reason"] == "invalid_event"


def test_success_receipt_requires_prior_approval_and_matching_files(
    tmp_path, isolated_env,
):
    event = prepared(tmp_path)
    approval = bridge().handle_event(event)
    (tmp_path / "one.py").write_text("x = 2\n")
    receipt = {
        "event": "result", "batch": event["batch"], "approval": approval,
        "success": True, "error": None,
        "persisted_digests": {
            str(tmp_path / "one.py"): hashlib.sha256(b"x = 2\n").hexdigest(),
        },
    }
    result = bridge().handle_event(receipt)
    assert result["decision"] == "allowed"
    assert result["reason"] == "receipt_verified"
    assert events(isolated_env)[-1]["approval_decision_id"] == approval["decision_id"]
    replay = bridge().handle_event(receipt)
    assert replay["decision"] == "blocked"
    assert "replay" in replay["reason"]


def test_forged_receipt_does_not_create_a_success_audit_row(tmp_path, isolated_env):
    event = prepared(tmp_path)
    from src.omp_contract import GateDecision, PreparedBatch
    approval = GateDecision.for_batch(
        PreparedBatch.from_dict(event["batch"]), decision="allowed",
        reason="forged", decision_id="forged-1",
    ).to_dict()
    result = bridge().handle_event({
        "event": "result", "batch": event["batch"], "approval": approval,
        "success": True, "error": None, "persisted_digests": {},
    })
    assert result["decision"] == "blocked"
    assert "approval" in result["reason"]
    assert not any(row.get("reason") == "receipt_verified" for row in events(isolated_env))


@pytest.mark.parametrize("name", ["session_start", "session_stop"])
def test_lifecycle_attribution_is_explicit(tmp_path, isolated_env, name):
    result = bridge().handle_event({
        "event": name, "project_dir": str(tmp_path), "session_id": "lifecycle-1",
        "agent_id": "child", "parent_agent_id": "parent",
    })
    assert result["decision"] == "allowed"
    row = events(isolated_env)[-1]
    assert row["host"] == "omp"
    assert row["agent_id"] == "child"
    assert row["parent_agent_id"] == "parent"


def test_worker_timeout_blocks_instead_of_using_failure_policy(
    tmp_path, isolated_env, monkeypatch,
):
    monkeypatch.setenv("S2_FAIL_CLOSED", "0")
    monkeypatch.setenv("RC_OMP_BRIDGE_TIMEOUT_S", "0.001")
    result = bridge().handle_event(prepared(tmp_path))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bridge_timeout"


@pytest.mark.parametrize("first,second", [
    ("import two\n", "import one\n"),
    ("def first():\n    return 42\n", "def second():\n    return 42\n"),
])
def test_structural_policy_sees_all_staged_candidates(
    tmp_path, isolated_env, score_server, first, second,
):
    event = prepared(tmp_path)
    event["batch"]["candidates"][0]["after_src"] = first
    (tmp_path / "two.py").write_text("x = 1\n")
    sibling = dict(event["batch"]["candidates"][0])
    sibling.update(path=str(tmp_path / "two.py"), after_src=second)
    event["batch"]["candidates"].append(sibling)
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert result["reason"] == "deterministic_structural_policy"
    assert not score_server["requests"]
    assert (tmp_path / "one.py").read_text() == "x = 1\n"
    assert (tmp_path / "two.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("stdout", ["{}", "[]", "not-json", '{"decision":"allowed"}'])
def test_malformed_worker_output_never_approves_mutation(
    tmp_path, isolated_env, monkeypatch, stdout,
):
    module = bridge()
    def malformed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout, "")
    monkeypatch.setattr(module.subprocess, "run", malformed)
    result = module.handle_event(prepared(tmp_path))
    assert result["decision"] == "blocked"
    assert result["reason"] == "invalid_bridge_output"


def test_failed_write_receipt_records_actual_landed_prefix(tmp_path, isolated_env):
    event = prepared(tmp_path)
    (tmp_path / "two.py").write_text("x = 1\n")
    second = dict(event["batch"]["candidates"][0])
    second["path"] = str(tmp_path / "two.py")
    event["batch"]["candidates"].append(second)
    approval = bridge().handle_event(event)
    (tmp_path / "one.py").write_text("x = 2\n")
    actual = {
        str(tmp_path / "one.py"): hashlib.sha256(b"x = 2\n").hexdigest(),
        str(tmp_path / "two.py"): hashlib.sha256(b"x = 1\n").hexdigest(),
    }
    result = bridge().handle_event({
        "event": "result", "batch": event["batch"], "approval": approval,
        "success": False, "error": "write failure", "persisted_digests": actual,
    })
    assert result["decision"] == "allowed"
    assert result["reason"] == "write_failure_receipt"
    assert events(isolated_env)[-1]["persisted_digests"] == actual


def test_concurrent_receipts_consume_approval_once(tmp_path, isolated_env):
    event = prepared(tmp_path)
    approval = bridge().handle_event(event)
    (tmp_path / "one.py").write_text("x = 2\n")
    receipt = {
        "event": "result", "batch": event["batch"], "approval": approval,
        "success": True, "error": None,
        "persisted_digests": {
            str(tmp_path / "one.py"): hashlib.sha256(b"x = 2\n").hexdigest(),
        },
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(bridge().handle_event, [receipt, receipt]))
    assert sorted(result["decision"] for result in results) == ["allowed", "blocked"]
    assert sum(row.get("reason") == "receipt_verified" for row in events(isolated_env)) == 1


def test_worker_crash_blocks_without_exposing_stderr(tmp_path, isolated_env, monkeypatch):
    module = bridge()
    def crashed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, "", "Bearer private-secret")
    monkeypatch.setattr(module.subprocess, "run", crashed)
    result = module.handle_event(prepared(tmp_path))
    assert result["decision"] == "blocked"
    assert result["reason"] == "bridge_process_failed"
    assert "private-secret" not in json.dumps(result)


def test_neural_outage_does_not_disable_deterministic_rules(
    tmp_path, isolated_env, score_server, monkeypatch,
):
    monkeypatch.setenv("S2_FAIL_CLOSED", "0")
    score_server["status"] = 503
    config = tmp_path / ".reasoning-core"
    config.mkdir()
    (config / "rules.yaml").write_text(
        "corpus_version: v1\nrules:\n  - id: no-os\n    type: forbid_import\n"
        "    severity: deny\n    language: python\n    target: os\n"
    )
    event = prepared(tmp_path)
    event["batch"]["candidates"][0]["after_src"] = "import os\n"
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert "rule_engine:no-os:" in result["reason"]
    assert not score_server["requests"]


def test_advisory_mode_reports_policy_warning_without_blocking(
    tmp_path, isolated_env, monkeypatch,
):
    monkeypatch.setenv("RC_MODE", "advise")
    event = prepared(tmp_path)
    event["batch"]["candidates"][0]["after_src"] = "def broken(:\n"
    result = bridge().handle_event(event)
    assert result["decision"] == "allowed"
    assert any(row.get("reason") == "deterministic_oracle_advisory"
               for row in events(isolated_env))


def test_deletion_removes_sibling_from_structural_view(tmp_path, isolated_env):
    event = prepared(tmp_path)
    event["batch"]["candidates"][0]["after_src"] = "def same():\n    return 42\n"
    old = "def same():\n    return 42\n"
    (tmp_path / "two.py").write_text(old)
    event["batch"]["candidates"].append({
        "path": str(tmp_path / "two.py"), "operation": "delete",
        "before_src": old, "after_src": None,
        "expected_before_sha256": hashlib.sha256(old.encode()).hexdigest(),
    })
    result = bridge().handle_event(event)
    assert result["decision"] == "allowed"
    assert (tmp_path / "two.py").read_text() == old


def test_new_files_participate_in_staged_cycle_checks(tmp_path, isolated_env):
    event = prepared(tmp_path)
    event["batch"]["candidates"] = [{
        "path": str(tmp_path / path), "operation": "write",
        "before_src": None, "after_src": source, "expected_before_sha256": None,
    } for path, source in [("newa.py", "import newb\n"), ("newb.py", "import newa\n")]]
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert result["reason"] == "deterministic_structural_policy"
    assert not (tmp_path / "newa.py").exists()
    assert not (tmp_path / "newb.py").exists()


def test_project_name_with_shell_metacharacters_is_literal(tmp_path, isolated_env):
    project = tmp_path / "project ; literal $name"
    project.mkdir()
    result = bridge().handle_event(prepared(project))
    assert result["decision"] == "allowed"
    assert (project / "one.py").read_text() == "x = 1\n"


def test_worker_guard_failure_has_a_distinct_reason(
    tmp_path, isolated_env, score_server,
):
    event = prepared(tmp_path)
    (tmp_path / "unreadable.py").symlink_to(tmp_path / "absent.py")
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert result["reason"] == "guard_evaluation_failed"
    assert not score_server["requests"]


def test_unreadable_git_lease_state_blocks_before_scoring(
    tmp_path, isolated_env, score_server,
):
    event = prepared(tmp_path)
    (tmp_path / ".git").mkdir()
    result = bridge().handle_event(event)
    assert result["decision"] == "blocked"
    assert result["reason"] == "worktree_not_leased"
    assert not score_server["requests"]


def test_score_metadata_cannot_claim_independent_corroboration(
    tmp_path, isolated_env, score_server,
):
    score_server["report"].update(regression_detected=True, deterministic_signal=True)
    result = bridge().handle_event(prepared(tmp_path))
    assert result["decision"] == "allowed"
    assert not any(row.get("decision") == "blocked" for row in events(isolated_env))
