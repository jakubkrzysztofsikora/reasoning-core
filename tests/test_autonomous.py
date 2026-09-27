"""Behavior tests for autonomous task intake and bounded execution."""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from src.autonomous import (
    JevAdapter, LayaAdapter, LocalAdapter, TaskSpec, _checks, _enforcement_digest, _git,
    _codex_qualification, _final_admission, _patch_reproduces_final, _qualification,
    _run_bounded, _stage_check_workspace,
    _write_policy, run_task,
)


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
    for path in ("../outside.py", ".", "./PLAN.md", "src//service.py"):
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


def test_laya_default_uses_server_language_router():
    response = {"routing": {"model": "multilingual"}, "answers": {
        "task_kind": {"type": "choice", "choice": "localized", "answer_confidence": 0.8},
        "needs_deep_reasoning": {"type": "noul", "noul": 0.2},
    }}
    seen = {}

    def transport(request, timeout):
        seen.update(json.loads(request.data))
        return io.BytesIO(json.dumps(response).encode())

    decision = LayaAdapter(transport=transport).decide(_task())
    assert "model" not in seen
    assert decision.model == "multilingual"
    assert decision.status == "ok"


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
            "enforcement_sha256": _enforcement_digest(),
            "permission_mode": "bypassPermissions",
        },
    }))

    prompts = []

    def fake_agent(workspace, prompt, settings, env, budget, timeout):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"returncode": 1, "cost_usd": 0.01}
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
        check_runner=lambda ws, task: [{"returncode": 0 if "return 2" in
                                        (ws / "main.py").read_text() else 1}],
        host_version=lambda: "2.1.274 (Claude Code)",
        sidecar_health=lambda: True,
    )
    assert result["status"] == "completed"
    assert len(prompts) == 2
    assert all("Make main.py return 2." in prompt for prompt in prompts)
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
                     "enforcement_sha256": _enforcement_digest(),
                     "permission_mode": "bypassPermissions"},
    }))

    def ungated_agent(workspace, prompt, settings, env, budget, timeout):
        (workspace / "main.py").write_text("def value():\n    return 2\n")
        return {"returncode": 0}

    out = tmp_path / "run"
    result = run_task(
        _task(), repo, out, qualification, LocalAdapter(),
        model="claude-sonnet-4-5", invoke=ungated_agent,
        check_runner=lambda ws, task: [{"returncode": 0}],
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
                 model="claude-sonnet-4-5", host_version=lambda: "test-host")
    assert not (tmp_path / "run").exists()


def test_codex_qualification_requires_exact_bridge_model_and_host(tmp_path):
    from src.autonomous import _CODEX_HOOK

    report = tmp_path / "codex.json"
    valid = {"qualified": True, "model": "gpt-6-astra",
             "codex_version": "codex-cli 0.156.1",
             "bridge_sha256": hashlib.sha256(_CODEX_HOOK.read_bytes()).hexdigest(),
             "enforcement_sha256": _enforcement_digest(),
             "host_allowed_patch_observed": True,
             "direct_forbidden_patch_denied": True}
    report.write_text(json.dumps(valid))
    assert _codex_qualification(report, "gpt-6-astra", "codex-cli 0.156.1")["model"] == "gpt-6-astra"
    with pytest.raises(ValueError, match="qualification"):
        _codex_qualification(report, "gpt-6-sol", "codex-cli 0.156.1")
    with pytest.raises(ValueError, match="qualification"):
        _codex_qualification(report, "gpt-6-astra", "codex-cli 0.157.0")
    report.write_text(json.dumps({**valid, "host_allowed_patch_observed": False}))
    with pytest.raises(ValueError, match="qualification"):
        _codex_qualification(report, "gpt-6-astra", "codex-cli 0.156.1")


def test_codex_final_admission_rejects_forbidden_import_from_fresh_policy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def value():\n    return 1\n")
    policy = source / ".reasoning-core"
    policy.mkdir()
    (policy / "rules.yaml").write_text(
        "corpus_version: v1\nrules:\n"
        "  - id: no_os_import\n    type: forbid_import\n"
        "    severity: deny\n    language: python\n"
        "    target: os\n    message: os import is forbidden\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "main.py", ".reasoning-core/rules.yaml"],
                   cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-q", "-m", "base"],
                   cwd=source, check=True)
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "main.py").write_text("import os\n\ndef value():\n    return os.getenv('X')\n")
    admitted = _final_admission(source, _git(source, "rev-parse", "HEAD"),
                                workspace, _task(), ["main.py"],
                                tmp_path / "audit", "http://127.0.0.1:9")
    assert admitted is False


def test_codex_final_admission_accepts_allowed_change(tmp_path, monkeypatch):
    from src import autonomous

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def value():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "main.py"], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-q", "-m", "base"],
                   cwd=source, check=True)
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "main.py").write_text("def value():\n    return 2\n")
    original_env = autonomous._agent_env

    def symbolic_env(project, audit_root, sidecar_url, host):
        return {**original_env(project, audit_root, sidecar_url, host),
                "S2_FAIL_CLOSED": "0"}

    monkeypatch.setattr(autonomous, "_agent_env", symbolic_env)
    monkeypatch.setattr(autonomous, "_guard_allows", lambda *args: True)
    monkeypatch.setattr(autonomous, "_gated_final_paths", lambda *args: [])
    def clean_check(project, task):
        assert (project / "main.py").read_text() == "def value():\n    return 2\n"
        return [{"returncode": 0}]

    monkeypatch.setattr(autonomous, "_checks", clean_check)
    patches = []
    hashes = {}
    assert _final_admission(
        source, _git(source, "rev-parse", "HEAD"), workspace, _task(),
        ["main.py"], tmp_path / "audit", "http://127.0.0.1:9",
        admitted_hashes=hashes, accepted_patch=patches)
    assert "+    return 2" in patches[0]
    assert hashes == {"main.py": hashlib.sha256((workspace / "main.py").read_bytes()).hexdigest()}


def test_codex_final_admission_checks_clean_patch_without_ignored_helper(tmp_path, monkeypatch):
    from src import autonomous

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def value():\n    return 1\n")
    (source / ".gitignore").write_text("helper.py\n")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "main.py", ".gitignore"], cwd=source, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-q", "-m", "base"],
                   cwd=source, check=True)
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    (workspace / "main.py").write_text("def value():\n    return 3\n")
    (workspace / "helper.py").write_text("def override():\n    return True\n")
    original_env = autonomous._agent_env
    monkeypatch.setattr(autonomous, "_agent_env", lambda project, audit, url, host: {
        **original_env(project, audit, url, host), "S2_FAIL_CLOSED": "0"})
    monkeypatch.setattr(autonomous, "_guard_allows", lambda *args: True)
    monkeypatch.setattr(autonomous, "_gated_final_paths", lambda *args: [])

    def clean_check(project, task):
        assert not (project / "helper.py").exists()
        return [{"returncode": 1, "diagnostic": "missing required behavior"}]

    monkeypatch.setattr(autonomous, "_checks", clean_check)
    results = []
    assert not _final_admission(
        source, _git(source, "rev-parse", "HEAD"), workspace, _task(),
        ["main.py"], tmp_path / "audit", "http://127.0.0.1:9", results)
    assert results == [{"returncode": 1, "diagnostic": "missing required behavior"}]


def test_codex_run_rejects_unqualified_host_before_cloning(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({"qualified": False}))
    with pytest.raises(ValueError, match="qualification"):
        run_task(_task(), repo, tmp_path / "run", report, LocalAdapter(),
                 host="codex", model="gpt-6-astra",
                 host_version=lambda: "codex-cli 0.156.1")
    assert not (tmp_path / "run").exists()


def test_codex_invocation_uses_qualified_patch_hook_and_workspace_sandbox(tmp_path, monkeypatch):
    from src import autonomous

    seen = {}

    def bounded(command, *, cwd, env, timeout):
        seen.update(command=command, cwd=cwd, env=env, timeout=timeout)
        return 0, b'{"type":"turn.completed","usage":{"output_tokens":7}}\n', b""

    monkeypatch.setattr(autonomous, "_run_bounded", bounded)
    result = autonomous._invoke_codex(
        tmp_path, "Edit main.py", tmp_path / "unused.json", {"RC_HOST": "codex"},
        1.0, 30, "gpt-6-astra",
    )
    assert seen["command"][:4] == [
        "codex", "exec", "--ignore-user-config", "--dangerously-bypass-hook-trust",
    ]
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "workspace-write"
    assert "apply_patch" in " ".join(seen["command"])
    assert str(autonomous._CODEX_HOOK) in " ".join(seen["command"])
    assert result == {"returncode": 0, "cost_usd": None, "usage": {"output_tokens": 7}}


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
                     "enforcement_sha256": _enforcement_digest(),
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


@pytest.mark.parametrize("target", ["PLAN.md", ".reasoning-core"])
def test_policy_setup_rejects_symlink_destinations(tmp_path, target):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    if target == "PLAN.md":
        outside.write_text("original")
    else:
        outside.mkdir()
    (workspace / target).symlink_to(outside)
    with pytest.raises(ValueError, match="policy destination escapes"):
        _write_policy(workspace, _task())
    if target == "PLAN.md":
        assert outside.read_text() == "original"
    else:
        assert list(outside.iterdir()) == []


def test_qualification_rejects_stale_guard_digest(tmp_path):
    report = tmp_path / "qualification.json"
    report.write_text(json.dumps({
        "qualified": True, "qualified_tools": ["Edit", "Write"],
        "unavailable_tools": ["MultiEdit"], "manifest": {
            "model": "claude-sonnet-4-5", "claude_version": "test-host",
            "permission_mode": "bypassPermissions", "enforcement_sha256": "stale",
        },
    }))
    with pytest.raises(ValueError, match="qualification"):
        _qualification(report, "claude-sonnet-4-5", "test-host")


def test_exported_patch_preserves_trailing_spaces(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-q", "-m", "base"], cwd=repo, check=True)
    (repo / "main.py").write_text("VALUE = 2  \n")
    subprocess.run(["git", "add", "main.py"], cwd=repo, check=True)
    patch = _git(repo, "diff", "--cached", "--binary", "HEAD", strip=False)
    assert "+VALUE = 2  \n" in patch
    assert _patch_reproduces_final(repo, patch, ["main.py"])


def test_exported_patch_can_add_a_new_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "existing.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "existing.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-q", "-m", "base"], cwd=repo, check=True)
    (repo / "new.py").write_text("VALUE = 2\n")
    subprocess.run(["git", "add", "new.py"], cwd=repo, check=True)
    patch = _git(repo, "diff", "--cached", "--binary", "HEAD", strip=False)
    assert _patch_reproduces_final(repo, patch, ["new.py"])


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_cannot_write_outside_disposable_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    code = ("import os; fd = os.open(" + repr(str(outside))
            + ", os.O_WRONLY | os.O_TRUNC); os.close(fd)")
    result = _checks(workspace, _task(checks=[[sys.executable, "-c", code]]))
    assert result[0]["returncode"] != 0
    assert outside.read_text() == "original"
    assert "original" not in str(result)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_run_against_disposable_copy(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("VALUE = 2\n")
    result = _checks(workspace, _task(checks=[[sys.executable, "-c",
                                               "import main; assert main.VALUE == 2"]]))
    assert result[0]["returncode"] == 0
    assert (workspace / "main.py").read_text() == "VALUE = 2\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_record_non_utf8_output_as_bytes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = _checks(workspace, _task(checks=[[sys.executable, "-c",
                                               "import os; os.write(1, bytes([255]))"]]))
    assert result[0]["returncode"] == 0
    assert result[0]["output_sha256"] == hashlib.sha256(b"\xff").hexdigest()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_cannot_use_loopback_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        code = ("import socket; socket.create_connection((\"127.0.0.1\", "
                + str(port) + "), timeout=2)")
        result = _checks(workspace, _task(checks=[[sys.executable, "-c", code]]))
    assert result[0]["returncode"] != 0


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_cannot_connect_to_host_unix_socket(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with tempfile.TemporaryDirectory(prefix="rc-sock-", dir="/tmp") as directory:
        endpoint = Path(directory) / "control.sock"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            code = ("import socket; s = socket.socket(socket.AF_UNIX); s.connect("
                    + repr(str(endpoint)) + ")")
            result = _checks(workspace, _task(checks=[[sys.executable, "-c", code]]))
    assert result[0]["returncode"] != 0


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec check")
def test_checks_cannot_signal_host_process(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = subprocess.Popen(["sleep", "30"])
    try:
        code = f"import os,signal; os.kill({sentinel.pid}, signal.SIGTERM)"
        result = _checks(workspace, _task(checks=[[sys.executable, "-c", code]]))
        assert result[0]["returncode"] != 0
        assert sentinel.poll() is None
    finally:
        if sentinel.poll() is None:
            sentinel.terminate()
        sentinel.wait(timeout=5)


def test_check_staging_rejects_external_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="unsafe check input symlink"):
        _stage_check_workspace(workspace, tmp_path / "scratch")


def test_bounded_process_terminates_child_group_on_timeout(tmp_path):
    code = ("import subprocess,time; p=subprocess.Popen(['sleep','30']); "
            "print(p.pid, flush=True); time.sleep(30)")
    rc, stdout, _ = _run_bounded([sys.executable, "-c", code], cwd=tmp_path,
                                  env={"PATH": os.environ.get("PATH", "")}, timeout=1)
    assert rc == 124
    child = int(stdout.strip())
    for _ in range(20):
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(child)],
                                capture_output=True, text=True).stdout.strip()
        if not status or status.startswith("Z"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out child survived its process group")


def test_bounded_process_limits_output_bytes(tmp_path):
    code = "import os; os.write(1, b'x' * 2_000_000)"
    rc, stdout, _ = _run_bounded([sys.executable, "-c", code], cwd=tmp_path,
                                  env={"PATH": os.environ.get("PATH", "")}, timeout=5)
    assert rc != 0
    assert len(stdout) <= 1_000_000


def test_bounded_output_does_not_limit_workspace_file_size(tmp_path):
    code = ("import os; fd=os.open('large.bin', os.O_WRONLY|os.O_CREAT, 0o600); "
            "os.write(fd, b'x' * 2_000_000); os.close(fd)")
    rc, stdout, stderr = _run_bounded([sys.executable, "-c", code], cwd=tmp_path,
                                       env={"PATH": os.environ.get("PATH", "")}, timeout=5)
    assert rc == 0, stderr
    assert not stdout
    assert (tmp_path / "large.bin").stat().st_size == 2_000_000


def test_bounded_process_does_not_wait_for_detached_pipe_holder(tmp_path):
    code = ("import subprocess,time; p=subprocess.Popen(['sleep','10'], "
            "start_new_session=True); print(p.pid, flush=True); time.sleep(30)")
    started = time.monotonic()
    rc, stdout, _ = _run_bounded([sys.executable, "-c", code], cwd=tmp_path,
                                  env={"PATH": os.environ.get("PATH", "")}, timeout=1)
    assert rc == 124
    assert time.monotonic() - started < 4
    child = int(stdout.strip())
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(child)],
                            capture_output=True, text=True).stdout.strip()
    assert not status or status.startswith("Z")


def test_cli_defaults_to_local_laya_router(monkeypatch, tmp_path):
    from src import rc_cli
    from src import autonomous

    task = tmp_path / "task.json"
    task.write_text(json.dumps({"prompt": "Edit main.py", "triage_brief": "Edit main.py",
                                "allowed_paths": ["main.py"], "checks": [["true"]]}))
    seen = {}

    def fake_run(task_spec, repo, out, qualification, adapter, **kwargs):
        seen["adapter"] = adapter
        return {"status": "completed", "workspace": str(tmp_path),
                "decision": {"source": "laya", "status": "ok"}}

    monkeypatch.setattr(autonomous, "run_task", fake_run)
    assert rc_cli.main(["autonomous", "--task", str(task), "--repo", str(tmp_path),
                        "--out", str(tmp_path / "out"), "--qualification",
                        str(tmp_path / "qualification.json")]) == 0
    assert isinstance(seen["adapter"], LayaAdapter)
    assert seen["adapter"].model is None
