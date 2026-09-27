"""Behavior tests for autonomous task intake and bounded execution."""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys

import pytest

from src.autonomous import JevAdapter, LayaAdapter, LocalAdapter, TaskSpec, run_task


def _task(**overrides):
    raw = {
        "prompt": "Make main.py return 2.",
        "triage_brief": "Small Python function update.",
        "allowed_paths": ["main.py"],
        "checks": [[sys.executable, "-c", "assert __import__('main').value() == 2"]],
    }
    raw.update(overrides)
    return TaskSpec.from_dict(raw)


def test_task_rejects_parent_paths():
    for path in ("../outside.py", "."):
        with pytest.raises(ValueError, match="allowed_paths"):
            _task(allowed_paths=[path])


def test_local_adapter_does_not_grant_write_authority():
    decision = LocalAdapter().decide(_task())
    assert decision.source == "local"
    assert decision.kind in {"localized", "multifile", "investigation", "uncertain"}
    assert not hasattr(decision, "allowed_paths")


def test_jev_adapter_parses_typed_response_without_leaking_key():
    response = {
        "model": "jev-1.13.0",
        "answers": {
            "task_kind": {
                "type": "choice", "choice": "localized",
                "probabilities": {"localized": 0.9, "multifile": 0.1, "investigation": 0.0},
                "confidence": 0.85,
            },
            "needs_deep_reasoning": {"type": "noul", "noul": 0.2},
        },
        "usage": {"input_tokens": 120},
    }
    seen = {}

    def transport(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["payload"] = json.loads(request.data)
        return io.BytesIO(json.dumps(response).encode())

    decision = JevAdapter("secret-for-test", transport=transport).decide(_task())
    assert decision.kind == "localized"
    assert decision.source == "jev"
    assert decision.confidence == 0.85
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["authorization"] == "Bearer secret-for-test"
    assert seen["payload"]["questions"]["task_kind"]["type"] == "choice"
    assert "secret-for-test" not in repr(decision)


def test_jev_failure_returns_uncertain():
    def unavailable(request, timeout):
        raise OSError("unavailable")

    decision = JevAdapter("secret-for-test", transport=unavailable).decide(_task())
    assert decision.kind == "uncertain"
    assert decision.source == "jev"
    assert decision.status == "unavailable"


def test_laya_adapter_uses_loopback_and_answer_probability():
    response = {
        "model": "laya-rl-agent",
        "routing": {"model": "english"},
        "answers": {
            "task_kind": {
                "type": "choice", "choice": "localized",
                "confidence": 0.53, "answer_confidence": 0.85,
            },
            "needs_deep_reasoning": {"type": "noul", "noul": 0.12},
        },
        "usage": {"input_tokens": 132},
    }
    seen = {}

    def transport(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["payload"] = json.loads(request.data)
        return io.BytesIO(json.dumps(response).encode())

    decision = LayaAdapter(
        "http://127.0.0.1:8000", model="english", transport=transport,
    ).decide(_task())
    assert decision.source == "laya"
    assert decision.kind == "localized"
    assert decision.confidence == 0.85
    assert decision.needs_deep_reasoning == 0.12
    assert decision.model == "english"
    assert seen["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert seen["authorization"] is None
    assert seen["payload"]["model"] == "english"


def test_laya_adapter_rejects_nonlocal_endpoint_and_fails_to_uncertain():
    with pytest.raises(ValueError, match="loopback"):
        LayaAdapter("https://example.com", model="english")

    def unavailable(request, timeout):
        raise OSError("offline")

    decision = LayaAdapter(
        "http://localhost:8000", model="english", transport=unavailable,
    ).decide(_task())
    assert decision.kind == "uncertain"
    assert decision.source == "laya"
    assert decision.status == "unavailable"

def test_laya_malformed_response_returns_uncertain():
    response = {
        "answers": {
            "task_kind": {
                "type": "choice", "choice": "localized",
                "answer_confidence": 0.9,
            },
            "needs_deep_reasoning": {"type": "noul", "noul": 0.2},
        },
        "routing": ["unexpected"],
    }

    def transport(request, timeout):
        return io.BytesIO(json.dumps(response).encode())

    decision = LayaAdapter(transport=transport).decide(_task())
    assert decision.kind == "uncertain"
    assert decision.status == "unavailable"

def test_run_task_keeps_source_repo_untouched_and_emits_patch(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "main.py").write_text("def value():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "seed"], cwd=repo, check=True,
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "qualified": True,
        "qualified_tools": ["Edit", "Write"],
        "unavailable_tools": ["MultiEdit"],
        "manifest": {
            "model": "claude-sonnet-4-5",
            "claude_version": "2.1.274 (Claude Code)",
            "permission_mode": "bypassPermissions",
        },
    }))

    def fake_agent(workspace, prompt, settings, env, budget, timeout):
        assert "main.py" in prompt
        (workspace / "main.py").write_text("def value():\n    return 2\n")
        audit = out / "audit" / "2026-09-27"
        audit.mkdir(parents=True)
        (audit / "session.jsonl").write_text(json.dumps({
            "decision": "allowed", "tool_name": "Edit",
            "project_dir": str(workspace), "file_path_rel": "main.py",
            "after_sha256": hashlib.sha256((workspace / "main.py").read_bytes()).hexdigest(),
        }) + "\n")
        return {"returncode": 0, "cost_usd": 0.01}

    out = tmp_path / "run"
    result = run_task(
        _task(), repo, out, qualification, LocalAdapter(),
        model="claude-sonnet-4-5", invoke=fake_agent,
        host_version=lambda: "2.1.274 (Claude Code)",
        sidecar_health=lambda: True,
    )
    assert result["status"] == "completed"
    assert (repo / "main.py").read_text() == "def value():\n    return 1\n"
    assert "+    return 2" in (out / "final.patch").read_text()
    assert result["changed_paths"] == ["main.py"]


def test_run_task_rejects_final_file_without_matching_gate_receipt(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "main.py").write_text("def value():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "seed"], cwd=repo, check=True,
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "qualified": True, "qualified_tools": ["Edit", "Write"],
        "unavailable_tools": ["MultiEdit"],
        "manifest": {"model": "claude-sonnet-4-5",
                     "claude_version": "2.1.274 (Claude Code)",
                     "permission_mode": "bypassPermissions"},
    }))

    def ungated_agent(workspace, prompt, settings, env, budget, timeout):
        (workspace / "main.py").write_text("def value():\n    return 2\n")
        return {"returncode": 0}

    out = tmp_path / "run"
    result = run_task(
        _task(), repo, out, qualification, LocalAdapter(),
        model="claude-sonnet-4-5", invoke=ungated_agent,
        host_version=lambda: "2.1.274 (Claude Code)",
        sidecar_health=lambda: True,
    )
    assert result["status"] == "ungated_change"
    assert result["attempts"][0]["missing_gate_receipts"] == ["main.py"]
    assert not (out / "final.patch").exists()

def test_run_task_refuses_unqualified_host_before_cloning(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({"qualified": False, "manifest": {}}))
    with pytest.raises(ValueError, match="qualification"):
        run_task(_task(), repo, tmp_path / "run", report, LocalAdapter(),
                 model="claude-sonnet-4-5")
    assert not (tmp_path / "run").exists()


def test_agent_cannot_replace_allowed_file_with_external_symlink(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "main.py").write_text("def value():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "seed"], cwd=repo, check=True,
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "qualified": True, "qualified_tools": ["Edit", "Write"],
        "unavailable_tools": ["MultiEdit"],
        "manifest": {"model": "claude-sonnet-4-5",
                     "claude_version": "2.1.274 (Claude Code)",
                     "permission_mode": "bypassPermissions"},
    }))
    outside = tmp_path / "outside.py"
    outside.write_text("def value():\n    return 2\n")

    def fake_agent(workspace, prompt, settings, env, budget, timeout):
        (workspace / "main.py").unlink()
        (workspace / "main.py").symlink_to(outside)
        return {"returncode": 0}

    result = run_task(
        _task(), repo, tmp_path / "run", qualification, LocalAdapter(),
        model="claude-sonnet-4-5", invoke=fake_agent,
        host_version=lambda: "2.1.274 (Claude Code)",
        sidecar_health=lambda: True,
    )
    assert result["status"] == "policy_violation"
    assert not (tmp_path / "run" / "final.patch").exists()
