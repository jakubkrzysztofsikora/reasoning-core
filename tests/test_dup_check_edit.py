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


def test_trivial_helper_in_three_places_stays_note_only(tmp_path):
    repo = _repo(tmp_path)
    for n in ("a", "b", "c"):
        (repo / f"{n}.test.ts").write_text(SLUG.format(name=f"slug_{n}"))
    _, rows = _check(repo, "a.test.ts", {"a.test.ts"}, tmp_path)
    [row] = rows.values()
    assert row["sites"] == 3
    assert row["verdict"] == NOTE
