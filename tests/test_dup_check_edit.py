"""Routing + ledger writes for one edit (src/duplicate_detection/check.py::check_edit).

Offline: a stub embedder over a tmp_path .ts repo; `changed` is passed in, so no git.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection import findings as ledger  # noqa: E402
from src.duplicate_detection.check import EXTRACT, NOTE, check_edit  # noqa: E402
from src.duplicate_detection.index import logic_tokens  # noqa: E402
from src.duplicate_detection.repo_index import build_dup_index  # noqa: E402

_VOCAB = 64

SLUG = 'export function {name}(s) {{\n  return s.toLowerCase().replace(RE, "-").trim();\n}}\n'
DISMISS = (
    "export function {name}(ref, onClose) {{\n"
    "  function onDoc(e) {{ if (ref.current && !ref.current.contains(e.target)) onClose(); }}\n"
    "  function onKey(e) {{ if (e.key === 'Escape') onClose(); }}\n"
    "  document.addEventListener('mousedown', onDoc);\n"
    "  document.addEventListener('keydown', onKey);\n"
    "  return () => {{ document.removeEventListener('mousedown', onDoc); "
    "document.removeEventListener('keydown', onKey); }};\n"
    "}}\n"
)


def _stub_embed(source: str) -> np.ndarray:
    vec = np.zeros(_VOCAB, dtype=np.float32)
    for tok in logic_tokens("x.ts", source):
        vec[sum(ord(c) for c in tok) % _VOCAB] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _check(repo: Path, rel: str, changed, tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    text = check_edit(
        str(repo / rel), (repo / rel).read_text(), index,
        embed_fn=_stub_embed, repo_root=str(repo), changed=changed, ledger_file=path,
    )
    return text, ledger.load(path)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def test_relocation_gives_no_message_and_is_recorded_as_a_move(tmp_path):
    # initialsOf-style: copied out of a.ts (being edited) into new d.ts.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(SLUG.format(name="toSlug"))
    text, rows = _check(repo, "d.ts", {"a.ts", "d.ts"}, tmp_path)
    assert text is None
    [row] = rows.values()
    assert row["likely_move"] is True
    assert row["verdict"] == NOTE


def test_match_outside_the_diff_is_a_tracked_follow_up_not_a_message(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    text, rows = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    assert text is None
    [row] = rows.values()
    assert row["in_diff"] is False
    assert row["likely_move"] is False
    assert row["status"] == "open"


def test_duplicate_inside_the_edited_file_is_shown_inline_with_the_rule(tmp_path):
    repo = _repo(tmp_path)
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug") + SLUG.format(name="toSlug"))
    text, rows = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    assert text is not None
    assert "makeSlug" in text and "toSlug" in text
    assert "2 sites" in text
    assert "[shares " in text and "toLowerCase" in text  # the evidence, not just a score
    assert "otherwise proceed" not in text
    assert all(r["in_diff"] for r in rows.values())


def test_same_finding_is_not_repeated_inline_on_the_next_edit(tmp_path):
    repo = _repo(tmp_path)
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug") + SLUG.format(name="toSlug"))
    first, _ = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    second, _ = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    assert first is not None
    assert second is None


def test_three_covered_nontrivial_sites_are_recorded_as_extract(tmp_path):
    # onDocClick: same click-outside + Escape logic in 3 menus, all tested.
    repo = _repo(tmp_path)
    for menu in ("AccountMenu", "BasemapControl", "SortMenu"):
        (repo / f"{menu}.ts").write_text(DISMISS.format(name=f"dismiss{menu}"))
        (repo / f"{menu}.test.ts").write_text(f"import {{ {menu} }} from './{menu}'")
    text, rows = _check(repo, "AccountMenu.ts", {"AccountMenu.ts"}, tmp_path)
    assert text is None  # matches are outside the diff -> surfaced at Stop
    # The nested onDoc/onKey helpers are findings too; check the outer one.
    [row] = [r for r in rows.values() if r["added_name"] == "dismissAccountMenu"]
    assert row["sites"] == 3
    assert row["test_covered"] is True
    assert row["verdict"] == EXTRACT


def test_duplication_already_in_the_file_before_the_edit_is_not_recorded(tmp_path):
    # The agent touches a file that already held a duplicate it didn't write.
    repo = _repo(tmp_path)
    before = SLUG.format(name="makeSlug") + SLUG.format(name="toSlug")
    (repo / "d.ts").write_text(before + "export const unrelated = 1;\n")
    path = tmp_path / "ledger.jsonl"
    text = check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(),
        build_dup_index(str(repo), embed_fn=_stub_embed),
        embed_fn=_stub_embed, repo_root=str(repo), changed={"d.ts"}, ledger_file=path,
        baseline=before,
    )
    assert text is None
    assert ledger.load(path) == {}


def test_a_copy_added_next_to_an_existing_function_is_still_caught(tmp_path):
    repo = _repo(tmp_path)
    before = SLUG.format(name="toSlug")
    (repo / "d.ts").write_text(before + SLUG.format(name="makeSlug"))
    path = tmp_path / "ledger.jsonl"
    text = check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(),
        build_dup_index(str(repo), embed_fn=_stub_embed),
        embed_fn=_stub_embed, repo_root=str(repo), changed={"d.ts"}, ledger_file=path,
        baseline=before,
    )
    assert text is not None and "makeSlug" in text
    assert len(ledger.load(path)) == 1


def test_a_same_named_copy_added_next_to_the_original_is_still_checked(tmp_path):
    # e.g. a second class gets the same `handle` method: one copy is new.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    before = SLUG.format(name="toSlug")
    (repo / "d.ts").write_text(before + SLUG.format(name="toSlug"))
    path = tmp_path / "ledger.jsonl"
    check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(),
        build_dup_index(str(repo), embed_fn=_stub_embed),
        embed_fn=_stub_embed, repo_root=str(repo), changed={"d.ts"}, ledger_file=path,
        baseline=before,
    )
    assert len(ledger.load(path)) == 1


def test_baseline_defaults_to_the_committed_file(tmp_path):
    import subprocess

    repo = _repo(tmp_path)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug") + SLUG.format(name="toSlug"))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "d.ts").write_text((repo / "d.ts").read_text() + "export const unrelated = 1;\n")
    text, rows = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    assert text is None
    assert rows == {}


def test_reports_every_finding_it_recorded_even_without_a_message(tmp_path):
    # The hook's audit event must not say "clean" for a silent follow-up.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    path = tmp_path / "ledger.jsonl"
    recorded: list[str] = []
    text = check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(),
        build_dup_index(str(repo), embed_fn=_stub_embed),
        embed_fn=_stub_embed, repo_root=str(repo), changed={"d.ts"}, ledger_file=path,
        recorded=recorded,
    )
    assert text is None
    assert recorded == list(ledger.load(path))


def test_paths_through_a_symlink_resolve_to_the_same_repo_file(tmp_path):
    # The hooks pass the repo's real path; the edited path may go through a link.
    repo = _repo(tmp_path)
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug") + SLUG.format(name="toSlug"))
    link = tmp_path / "link"
    link.symlink_to(repo)
    path = tmp_path / "ledger.jsonl"
    text = check_edit(
        str(link / "d.ts"), (repo / "d.ts").read_text(),
        build_dup_index(str(repo), embed_fn=_stub_embed),
        embed_fn=_stub_embed, repo_root=str(repo), changed={"d.ts"}, ledger_file=path,
    )
    assert text is not None
    assert {r["added_path"] for r in ledger.load(path).values()} <= {"d.ts"}


def test_file_outside_the_indexed_languages_is_unsupported(tmp_path):
    # Parseable but not indexed (e.g. Markdown): must not pass as "checked, clean".
    import pytest

    from src.grammars import UnsupportedLanguageError

    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "notes.md").write_text("# notes\n")
    with pytest.raises(UnsupportedLanguageError):
        _check(repo, "notes.md", {"notes.md"}, tmp_path)


def test_novel_function_records_nothing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(
        "export function quicksort(arr) {\n"
        "  if (arr.length < 2) return arr;\n"
        "  const p = arr[0];\n"
        "  return quicksort(arr.filter((x) => x < p));\n"
        "}\n"
    )
    text, rows = _check(repo, "d.ts", {"d.ts"}, tmp_path)
    assert text is None
    assert rows == {}


def test_empty_source_or_index_is_silent(tmp_path):
    repo = _repo(tmp_path)
    empty_index = build_dup_index(str(repo), embed_fn=_stub_embed)
    kw = dict(embed_fn=_stub_embed, repo_root=str(repo), changed=set(), ledger_file=tmp_path / "l.jsonl")
    assert check_edit(str(repo / "d.ts"), SLUG.format(name="f"), empty_index, **kw) is None
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    assert check_edit(str(repo / "d.ts"), "", index, **kw) is None


def test_a_function_is_never_its_own_duplicate(tmp_path):
    # The edited file is in the index (absolute payload path vs relative index
    # paths): toSlug must not match itself, only slugify in b.ts.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "b.ts").write_text(SLUG.format(name="slugify"))
    _, rows = _check(repo, "a.ts", {"a.ts"}, tmp_path)
    [row] = rows.values()
    assert [(m["path"], m["name"]) for m in row["matches"]] == [("b.ts", "slugify")]


def test_same_name_in_another_file_is_still_a_duplicate(tmp_path):
    # Self-skip keys on path AND name; skipping on name alone would hide this.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="slug"))
    (repo / "b.ts").write_text(SLUG.format(name="slug"))
    _, rows = _check(repo, "b.ts", {"b.ts"}, tmp_path)
    [row] = rows.values()
    assert [m["path"] for m in row["matches"]] == ["a.ts"]


def test_reuses_index_vectors_instead_of_re_embedding_the_file(tmp_path):
    # The post-edit index refresh already embedded the edited file.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    calls = []

    def counting_embed(src):
        calls.append(src)
        return _stub_embed(src)

    check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(), index,
        embed_fn=counting_embed, repo_root=str(repo), changed={"d.ts"},
        ledger_file=tmp_path / "ledger.jsonl",
    )
    assert calls == []


def test_embeds_a_function_the_index_does_not_have(tmp_path):
    # Stale index (built before the edit): the new function must still be checked.
    repo = _repo(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    index = build_dup_index(str(repo), embed_fn=_stub_embed)
    (repo / "d.ts").write_text(SLUG.format(name="makeSlug"))
    calls = []

    def counting_embed(src):
        calls.append(src)
        return _stub_embed(src)

    check_edit(
        str(repo / "d.ts"), (repo / "d.ts").read_text(), index,
        embed_fn=counting_embed, repo_root=str(repo), changed={"d.ts"},
        ledger_file=tmp_path / "ledger.jsonl",
    )
    assert len(calls) == 1
    assert ledger.load(tmp_path / "ledger.jsonl")  # still found the duplicate


def test_trivial_helper_in_three_places_stays_note_only(tmp_path):
    repo = _repo(tmp_path)
    for n in ("a", "b", "c"):
        (repo / f"{n}.test.ts").write_text(SLUG.format(name=f"slug_{n}"))
    _, rows = _check(repo, "a.test.ts", {"a.test.ts"}, tmp_path)
    [row] = rows.values()
    assert row["sites"] == 3
    assert row["verdict"] == NOTE
