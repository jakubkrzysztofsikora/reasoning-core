"""Runtime contract of the PostToolUse hook (src/hooks/post_edit_duplicate_check.py).

Offline: a stub embedder + a stub-built index, no torch. The routing and rule
logic live in src/duplicate_detection/check.py (tests/test_dup_check*.py); this
file covers main(): opt-in, fail-open, the emitted message, and the audit event
written on every run.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection.index import logic_tokens  # noqa: E402
from src.duplicate_detection.repo_index import build_dup_index  # noqa: E402
from src.hooks import audit_log  # noqa: E402
from src.hooks import post_edit_duplicate_check as hook  # noqa: E402

_VOCAB = 64
SLUG = 'export function {name}(s) {{\n  return s.toLowerCase().replace(RE, "-").trim();\n}}\n'


def _stub_embed(source: str) -> np.ndarray:
    vec = np.zeros(_VOCAB, dtype=np.float32)
    for tok in logic_tokens("x.ts", source):
        vec[sum(ord(c) for c in tok) % _VOCAB] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated audit root + ledger cache; feature on."""
    audit = tmp_path / "audit"
    monkeypatch.setattr(audit_log, "_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("RC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(hook, "_embedder", lambda: _stub_embed)
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, audit


def _run(monkeypatch, payload) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    return exc.value.code


def _events(audit: Path) -> list[dict]:
    rows = []
    for f in sorted(audit.glob("*/*.jsonl")):
        rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("event_type") == "dup_check"]


def _payload(repo: Path, rel: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(repo / rel)}, "cwd": str(repo)}


def test_opt_out_is_silent_and_never_builds_the_index(env, monkeypatch, capsys):
    repo, audit = env
    monkeypatch.delenv("RC_DUP_ORACLE")
    touched = []
    monkeypatch.setattr(hook, "_get_index", lambda root: touched.append(root))
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    assert _run(monkeypatch, _payload(repo, "d.ts")) == 0
    assert touched == []
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert _events(audit) == []


def test_duplicate_in_the_edited_file_emits_a_post_tool_use_message(env, monkeypatch, capsys):
    repo, audit = env
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug") + SLUG.format(name="toSlug"))
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    monkeypatch.setattr(hook, "_get_index", lambda root: index)
    assert _run(monkeypatch, _payload(repo, "d.ts")) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUse"
    assert "makeSlug" in out["additionalContext"] and "toSlug" in out["additionalContext"]
    [event] = _events(audit)
    assert event["decision"] == "found"
    assert isinstance(event["latency_ms"], int)


def test_cross_file_duplicate_is_recorded_without_a_message(env, monkeypatch, capsys):
    repo, audit = env
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    monkeypatch.setattr(hook, "_get_index", lambda root: index)
    monkeypatch.setattr(hook.ledger, "changed_files", lambda root: {"d.ts"})
    assert _run(monkeypatch, _payload(repo, "d.ts")) == 0
    assert capsys.readouterr().out == ""
    [event] = _events(audit)
    assert event["decision"] == "clean"
    rows = hook.ledger.load(hook.ledger.ledger_path(str(repo)))
    assert [r["added_name"] for r in rows.values()] == ["makeSlug"]


def test_missing_file_is_logged_as_skipped(env, monkeypatch, capsys):
    repo, audit = env
    assert _run(monkeypatch, _payload(repo, "gone.ts")) == 0
    assert capsys.readouterr().out == ""
    [event] = _events(audit)
    assert event["decision"] == "skipped"


def test_garbage_payload_is_logged_as_skipped(env, monkeypatch, capsys):
    _, audit = env
    assert _run(monkeypatch, "not json at all") == 0
    assert capsys.readouterr().out == ""
    assert [e["decision"] for e in _events(audit)] == ["skipped"]


def test_failure_is_fail_open_but_traced(env, monkeypatch, capsys):
    repo, audit = env
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))

    def boom(_root):
        raise RuntimeError("index build blew up")

    monkeypatch.setattr(hook, "_get_index", boom)
    assert _run(monkeypatch, _payload(repo, "d.ts")) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "fail-open" in captured.err
    [event] = _events(audit)
    assert event["decision"] == "fail-open"
    assert "index build blew up" in event["error"]


def test_failed_import_is_surfaced_not_silent(env, monkeypatch, capsys):
    repo, audit = env
    monkeypatch.setattr(hook, "check_edit", None)
    assert _run(monkeypatch, _payload(repo, "d.ts")) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "inactive" in captured.err
    [event] = _events(audit)
    assert event["decision"] == "fail-open"


def test_get_index_uses_the_persisted_builder(monkeypatch):
    # A fresh hook process must reuse the on-disk cache, not re-embed the repo.
    hook._INDEX_CACHE.clear()
    sentinel = object()
    calls = []
    monkeypatch.setattr(hook, "load_or_build_dup_index", lambda root: calls.append(root) or sentinel)
    try:
        assert hook._get_index("/r") is sentinel
        assert hook._get_index("/r") is sentinel
    finally:
        hook._INDEX_CACHE.clear()
    assert calls == ["/r"]
