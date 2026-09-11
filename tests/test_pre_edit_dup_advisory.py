"""Unit tests for the advisory hook's pure pieces (src/hooks/pre_edit_dup_advisory.py).

Offline: a stub embedder + a stub-built index, no torch, no stdin. Covers the
added-source extraction and the advise() decision (flags a duplicate, silent on
novel code).
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

from src.dup_index import logic_tokens  # noqa: E402
from src.dup_repo_index import build_dup_index  # noqa: E402
from src.hooks import pre_edit_dup_advisory as hook  # noqa: E402
from src.hooks.pre_edit_dup_advisory import _added_source, advise  # noqa: E402

_VOCAB = 64


def _stub_embed(source: str) -> np.ndarray:
    vec = np.zeros(_VOCAB, dtype=np.float32)
    for tok in logic_tokens("x.ts", source):
        vec[sum(ord(c) for c in tok) % _VOCAB] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _index(tmp_path: Path):
    (tmp_path / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (tmp_path / "c.ts").write_text(
        'export function fib(n) {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n}\n'
    )
    return build_dup_index(str(tmp_path), embed_fn=_stub_embed)


def test_added_source_handles_write_edit_and_multiedit():
    assert _added_source({"content": "WRITE"}) == "WRITE"
    assert _added_source({"new_string": "EDIT"}) == "EDIT"
    both = _added_source({"edits": [{"new_string": "A"}, {"new_string": "B"}]})
    assert "A" in both and "B" in both
    assert _added_source({}) == ""


def test_advise_flags_a_function_that_duplicates_an_indexed_one(tmp_path):
    index = _index(tmp_path)
    # Agent writes a new file whose function duplicates toSlug's logic.
    added = 'export function makeSlug(t) {\n  return t.toLowerCase().replace(RE, "-").trim();\n}\n'
    text = advise(added, "d.ts", index, embed_fn=_stub_embed)
    assert text is not None
    assert "makeSlug" in text and "toSlug" in text
    assert "advisory only" in text


def test_advise_is_silent_on_a_novel_function(tmp_path):
    index = _index(tmp_path)
    added = (
        "export function quicksort(arr) {\n"
        "  if (arr.length < 2) return arr;\n"
        "  const p = arr[0];\n"
        "  return quicksort(arr.filter((x) => x < p));\n"
        "}\n"
    )
    assert advise(added, "d.ts", index, embed_fn=_stub_embed) is None


def test_advise_empty_index_or_source_is_silent(tmp_path):
    index = _index(tmp_path)
    assert advise("", "d.ts", index, embed_fn=_stub_embed) is None  # no source
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    empty_index = build_dup_index(str(empty_dir), embed_fn=_stub_embed)
    assert advise("export function f(){ return 1 + 1 + 1; }", "d.ts", empty_index, embed_fn=_stub_embed) is None


def test_advise_skips_self_when_editing_an_indexed_file(tmp_path):
    # Index has a.ts::toSlug and b.ts::slugify (a duplicate of toSlug).
    (tmp_path / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (tmp_path / "b.ts").write_text(
        'export function slugify(v) {\n  return v.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    # Re-adding toSlug to a.ts, addressed by ABSOLUTE path (as the hook receives
    # it): must skip a.ts::toSlug (self) but still flag slugify in b.ts.
    added = 'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    text = advise(added, str(tmp_path / "a.ts"), index, embed_fn=_stub_embed, repo_root=str(tmp_path))
    assert text is not None
    assert "b.ts" in text        # the genuine cross-file duplicate is surfaced
    assert "a.ts" not in text    # the file's own entry was skipped (no self-dup)


def test_main_opt_out_never_builds_the_index(monkeypatch, capsys):
    # Opt-out (env unset) must exit BEFORE touching the model/index, even for a
    # real (indexable) function. Asserting empty stdout alone was insufficient --
    # a sub-40-char payload emits nothing regardless of the opt-in.
    monkeypatch.delenv("RC_DUP_ORACLE", raising=False)
    touched = []
    monkeypatch.setattr(hook, "_get_index", lambda root: touched.append(root))
    monkeypatch.setattr(hook, "_embedder", lambda: (lambda s: None))
    payload = {
        "tool_input": {
            "file_path": "x.ts",
            "content": "export function realOne(s) {\n  return s.toLowerCase().replace(RE, '-').trim();\n}\n",
        },
        "cwd": "/tmp",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    assert exc.value.code == 0
    assert touched == []  # opt-out -> _get_index never called
    captured = capsys.readouterr()
    assert captured.out == "" and "dup-oracle" not in captured.err  # off -> fully silent


def test_main_fails_open_on_garbage_payload(monkeypatch, capsys):
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    with pytest.raises(SystemExit) as exc:
        hook.main()  # empty payload -> exits before building the index (no torch)
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_emit_additional_context_shape(capsys):
    hook._emit_additional_context("hi there", "PreToolUse")
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["additionalContext"] == "hi there"


# --- main() happy path + fail-open (the runtime contract) ---------------------

_SLUG_BODY = 'return {v}.toLowerCase().replace(RE, "-").trim();'


def test_main_emits_advisory_on_a_real_duplicate(tmp_path, monkeypatch, capsys):
    # Full main() path, offline: stubbed index + embedder. Adding makeSlug (a
    # duplicate of the indexed toSlug) must produce an additionalContext advisory.
    (tmp_path / "a.ts").write_text("export function toSlug(s) {\n  " + _SLUG_BODY.format(v="s") + "\n}\n")
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(hook, "_get_index", lambda root: index)
    monkeypatch.setattr(hook, "_embedder", lambda: _stub_embed)
    payload = {
        "tool_input": {
            "file_path": str(tmp_path / "d.ts"),
            "content": "export function makeSlug(t) {\n  " + _SLUG_BODY.format(v="t") + "\n}\n",
        },
        "cwd": str(tmp_path),
        "hookEventName": "PreToolUse",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert "toSlug" in out["hookSpecificOutput"]["additionalContext"]


def test_main_fails_open_when_the_work_raises(tmp_path, monkeypatch, capsys):
    # A real exception in the happy path (here: index build) must be swallowed
    # -> exit 0, no output. Narrowing the except would make this raise.
    monkeypatch.setenv("RC_DUP_ORACLE", "1")

    def boom(_root):
        raise RuntimeError("index build blew up")

    monkeypatch.setattr(hook, "_get_index", boom)
    monkeypatch.setattr(hook, "_embedder", lambda: _stub_embed)
    payload = {
        "tool_input": {"file_path": str(tmp_path / "d.ts"), "content": "export function f(x) { return x + 1; }\n"},
        "cwd": str(tmp_path),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""              # never blocks / never emits advisory
    assert "skipped" in captured.err       # ...but the swallowed failure is traced


def test_main_bails_out_cleanly_if_the_feature_failed_to_import(monkeypatch, capsys):
    # If the feature's modules failed to import (names guarded to None at module
    # load), the hook must exit 0 without doing work -- never crash or block.
    monkeypatch.setenv("RC_DUP_ORACLE", "1")
    monkeypatch.setattr(hook, "build_dup_index", None)
    payload = {
        "tool_input": {"file_path": "x.ts", "content": "export function f(x) { return x.toLowerCase(); }\n"},
        "cwd": "/tmp",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        hook.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""            # never blocks / never emits advisory
    # ...but enabled-yet-inactive (import failed) is surfaced, not silent.
    assert "inactive" in captured.err


# --- self-skip is a COMPOUND key: path AND name -------------------------------


def test_advise_flags_a_same_named_duplicate_in_another_file(tmp_path):
    # Same function name, DIFFERENT file -> a real cross-file duplicate, must be
    # flagged. (Skipping on name alone would silence it.)
    (tmp_path / "a.ts").write_text("export function slug(s) {\n  " + _SLUG_BODY.format(v="s") + "\n}\n")
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    added = "export function slug(t) {\n  " + _SLUG_BODY.format(v="t") + "\n}\n"
    text = advise(added, str(tmp_path / "b.ts"), index, embed_fn=_stub_embed, repo_root=str(tmp_path))
    assert text is not None and "a.ts" in text


def test_advise_flags_a_duplicate_within_the_edited_file(tmp_path):
    # Editing a.ts to ADD tidy(), which duplicates the existing clean() in the
    # SAME file -> different name, must be flagged. (Skipping on path alone would
    # silence every function in the edited file.)
    (tmp_path / "a.ts").write_text("export function clean(s) {\n  " + _SLUG_BODY.format(v="s") + "\n}\n")
    index = build_dup_index(str(tmp_path), embed_fn=_stub_embed)
    added = "export function tidy(t) {\n  " + _SLUG_BODY.format(v="t") + "\n}\n"
    text = advise(added, str(tmp_path / "a.ts"), index, embed_fn=_stub_embed, repo_root=str(tmp_path))
    assert text is not None and "clean" in text


# --- the hook uses the PERSISTED builder --------------------------------------


def test_get_index_delegates_to_the_persisted_builder(monkeypatch):
    # _get_index must build via the disk-persisted load_or_build_dup_index, NOT
    # the persistence-free build_dup_index, so a fresh hook process reuses the
    # on-disk cache instead of re-embedding the whole repo.
    hook._INDEX_CACHE.clear()
    sentinel = object()
    calls = {"persist": 0, "plain": 0}

    def fake_persist(root):
        calls["persist"] += 1
        return sentinel

    def fake_plain(root, **kwargs):
        calls["plain"] += 1
        return sentinel

    monkeypatch.setattr(hook, "load_or_build_dup_index", fake_persist)
    monkeypatch.setattr(hook, "build_dup_index", fake_plain)
    try:
        out = hook._get_index("/some/repo")
    finally:
        hook._INDEX_CACHE.clear()
    assert out is sentinel
    assert calls == {"persist": 1, "plain": 0}
