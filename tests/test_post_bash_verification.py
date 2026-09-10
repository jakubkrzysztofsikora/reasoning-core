from __future__ import annotations

import json

from src.hooks import post_bash_verification as hook


def test_classifies_deterministic_commands():
    assert hook._kind("pytest -q") == "test"
    assert hook._kind("ruff check src") == "lint"
    assert hook._kind("mypy src") == "typecheck"
    assert hook._kind("npm run build") == "build"
    assert hook._kind("python app.py") is None


def test_reads_host_exit_status_without_inventing_one():
    assert hook._exit_code({"tool_response": {"exit_code": 0}}) == 0
    assert hook._exit_code({"tool_response": {"exitCode": 2}}) == 2
    assert hook._exit_code({"tool_response": "failed"}) is None


def test_payload_rejects_non_bash_and_malformed_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps([])))
    assert hook._payload() == {}
