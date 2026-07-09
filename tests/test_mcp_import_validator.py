"""Tests for the validate_imports MCP tool.

Closes the gap documented in 2026-06-02-pain-feature-mapping.md §6:
forbid_import is deny-list only; this is the allow-list dual that
catches hallucinated modules / symbols without an explicit rule.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from src.mcp_import_validator import validate_imports
from src.project_index import clear_all_indices, _build_index, _PROJECT_INDEX_FUTURES, _PROJECT_INDEX_LOCK


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A tiny repo so the project index has something to resolve against."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.py").write_text(textwrap.dedent("""
        def parse_iso8601(s: str) -> str:
            return s

        class DateParser:
            pass
    """).strip())
    (tmp_path / "src" / "core.py").write_text(textwrap.dedent("""
        from src.utils import parse_iso8601

        def main():
            return parse_iso8601("2026-01-01")
    """).strip())
    return tmp_path


@pytest.fixture
def session(monkeypatch, fake_repo):
    """Prime the project index for the test repo."""
    clear_all_indices()
    sid = f"test-session-{os.getpid()}"
    monkeypatch.setenv("RC_SESSION_ID", sid)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(fake_repo))
    # Seed the index synchronously so the test isn't racy.
    idx = _build_index(str(fake_repo), sid)
    from concurrent.futures import Future
    fut: "Future" = Future()
    fut.set_result(idx)
    with _PROJECT_INDEX_LOCK:
        _PROJECT_INDEX_FUTURES[sid] = fut
    yield sid
    clear_all_indices()


def test_empty_source_is_ok():
    r = validate_imports("", "python")
    assert r["ok"] is True
    assert r["notes"] == ["empty_source"]


def test_no_session_returns_ok(monkeypatch):
    """Without a session id we fail-open — the gate is augmentation, not authority."""
    monkeypatch.delenv("RC_SESSION_ID", raising=False)
    r = validate_imports("import os\n", "python")
    assert r["ok"] is True
    assert "no_session_id" in r["notes"]


def test_stdlib_imports_resolve(session):
    r = validate_imports("import os\nimport sys\nfrom pathlib import Path\n",
                         "python")
    assert r["ok"] is True
    assert r["resolved_count"] == 3
    assert r["unresolved"] == []


def test_known_third_party_resolves(session):
    r = validate_imports("import torch\nimport pandas as pd\n", "python")
    assert r["ok"] is True
    assert r["resolved_count"] == 2


def test_repo_module_resolves(session):
    r = validate_imports("from src.utils import parse_iso8601\n", "python")
    assert r["ok"] is True, f"got {r}"
    assert r["resolved_count"] == 1
    assert r["unresolved"] == []


def test_unknown_symbol_in_known_module_flagged(session):
    """from src.utils import parse_iso9999 — module exists, symbol does not."""
    r = validate_imports("from src.utils import parse_iso9999\n", "python")
    assert r["ok"] is False, f"got {r}"
    assert any(u["kind"] == "unknown_symbol" for u in r["unresolved"])
    flagged = next(u for u in r["unresolved"] if u["kind"] == "unknown_symbol")
    assert flagged["name"] == "parse_iso9999"
    # Should suggest the near-miss.
    assert "parse_iso8601" in flagged["suggestion"]


def test_unknown_module_flagged(session):
    """import totally_made_up_module — not stdlib, not third-party, not in repo."""
    r = validate_imports("import totally_made_up_module\n", "python")
    assert r["ok"] is False
    flagged = next(u for u in r["unresolved"] if u["kind"] == "unknown_module")
    assert flagged["module"] == "totally_made_up_module"


def test_syntax_error_does_not_raise(session):
    """Broken Python should return ok with empty imports, not raise."""
    r = validate_imports("def broken(:\n    pass\n", "python")
    assert isinstance(r, dict)
    # No imports extractable → resolved_count == 0, no unresolved.
    assert r["resolved_count"] == 0


def test_typescript_relative_import_checked(session):
    """Relative TS imports are treated as repo-local and checked."""
    r = validate_imports(
        "import { foo } from './nonexistent';\n", "typescript"
    )
    # nonexistent path → unknown_module
    assert r["ok"] is False


def test_typescript_bare_specifier_resolves(session):
    """Bare npm packages assumed resolvable (we don't read package.json)."""
    r = validate_imports("import React from 'react';\n", "typescript")
    assert r["ok"] is True


def test_unsupported_language(session):
    r = validate_imports("foo", "ruby")
    assert r["ok"] is True
    assert any("unsupported_language" in n for n in r["notes"])
