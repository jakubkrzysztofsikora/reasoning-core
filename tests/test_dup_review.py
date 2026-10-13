"""Session-end review of the findings ledger (src/duplicate_detection/review.py).

Deterministic: tree-sitter + logic-token ratio over files in tmp_path, no model.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection import findings as ledger  # noqa: E402
from src.duplicate_detection.check import EXTRACT, NOTE  # noqa: E402
from src.duplicate_detection.review import MAX_BLOCKS, stop_decision  # noqa: E402

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
MENUS = ("AccountMenu", "BasemapControl", "SortMenu")


def _finding(added, matches, *, verdict=NOTE, likely_move=False, covered=False, shared=()):
    return ledger.make_finding(
        added_path=added[0], added_name=added[1],
        matches=[
            {"path": p, "name": n, "lineno": 1, "logic": 1.0, "shared": list(shared)}
            for p, n in matches
        ],
        verdict=verdict, in_diff=False, likely_move=likely_move, test_covered=covered,
    )


def _menus(repo: Path) -> dict:
    for m in MENUS:
        (repo / f"{m}.ts").write_text(DISMISS.format(name=f"dismiss{m}"))
        (repo / f"{m}.test.ts").write_text(f"import {{ {m} }} from './{m}'")
    return _finding(
        ("AccountMenu.ts", "dismissAccountMenu"),
        [(f"{m}.ts", f"dismiss{m}") for m in MENUS[1:]],
        verdict=EXTRACT, covered=True, shared=("addEventListener", "Escape"),
    )


def _setup(tmp_path, *findings):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    path = tmp_path / "ledger.jsonl"
    for f in findings:
        ledger.record(path, f)
    return repo, path


def test_empty_ledger_does_not_block(tmp_path):
    repo, path = _setup(tmp_path)
    d = stop_decision(str(repo), path)
    assert d.block is False and d.message == ""


def test_open_extract_finding_blocks_with_the_finding_and_how_to_defer(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    d = stop_decision(str(repo), path)
    assert d.block is True
    assert f["id"] in d.message
    assert "3 sites" in d.message
    assert "rc dup-defer" in d.message
    assert "shares " in d.message and "addEventListener" in d.message


def test_extracted_duplicate_is_resolved_and_does_not_block(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    # The agent extracted a shared hook: the per-menu copies are gone.
    for m in MENUS:
        (repo / f"{m}.ts").write_text(f"import {{ useDismiss }} from './useDismiss'\nexport const {m} = 1;\n")
    d = stop_decision(str(repo), path)
    assert d.block is False
    assert ledger.load(path)[f["id"]]["status"] == "resolved"


def test_completed_move_is_dropped(tmp_path):
    # initialsOf moved Avatar.ts -> initials.ts, source copy deleted.
    repo, path = _setup(tmp_path)
    (repo / "initials.ts").write_text(SLUG.format(name="initialsOf"))
    (repo / "Avatar.ts").write_text("export const Avatar = 1;\n")
    f = _finding(("initials.ts", "initialsOf"), [("Avatar.ts", "initialsOf")], likely_move=True)
    ledger.record(path, f)
    stop_decision(str(repo), path)
    assert ledger.load(path)[f["id"]]["status"] == "resolved"


def test_move_whose_source_copy_survived_becomes_a_real_finding(tmp_path):
    repo, path = _setup(tmp_path)
    (repo / "initials.ts").write_text(SLUG.format(name="initialsOf"))
    (repo / "Avatar.ts").write_text(SLUG.format(name="initialsOf"))
    f = _finding(("initials.ts", "initialsOf"), [("Avatar.ts", "initialsOf")], likely_move=True)
    ledger.record(path, f)
    stop_decision(str(repo), path)
    row = ledger.load(path)[f["id"]]
    assert row["status"] == "open"
    assert row["likely_move"] is False


def test_deferred_finding_does_not_block(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    ledger.set_status(path, f["id"], "deferred", reason="menus diverge next sprint")
    assert stop_decision(str(repo), path).block is False


def test_notes_ride_along_but_never_block_alone(tmp_path):
    repo, path = _setup(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "b.ts").write_text(SLUG.format(name="makeSlug"))
    note = _finding(("b.ts", "makeSlug"), [("a.ts", "toSlug")])
    ledger.record(path, note)
    alone = stop_decision(str(repo), path)
    assert alone.block is False

    ledger.record(path, _menus(repo))
    both = stop_decision(str(repo), path)
    assert both.block is True
    assert "makeSlug" in both.message


def test_block_is_capped_then_escalated(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    blocks = [stop_decision(str(repo), path).block for _ in range(MAX_BLOCKS)]
    assert blocks == [True] * MAX_BLOCKS
    d = stop_decision(str(repo), path)
    assert d.block is False
    assert d.escalated == [f["id"]]
    assert ledger.load(path)[f["id"]]["status"] == "escalated"


def test_deferred_or_escalated_finding_resolves_once_the_duplicate_is_gone(tmp_path):
    # Otherwise the sticky status outlives the code, and a later reintroduction
    # of the same copies would stay silent for good.
    for status in ("deferred", "escalated"):
        (tmp_path / status).mkdir()
        repo, path = _setup(tmp_path / status)
        f = _menus(repo)
        ledger.record(path, f)
        ledger.set_status(path, f["id"], status, reason="r")
        for m in MENUS[1:]:
            (repo / f"{m}.ts").write_text(f"export const {m} = 1;\n")
        d = stop_decision(str(repo), path)
        assert d.resolved == [f["id"]]
        assert ledger.load(path)[f["id"]]["status"] == "resolved"


def test_deferred_finding_still_present_stays_deferred(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    ledger.set_status(path, f["id"], "deferred", reason="r")
    stop_decision(str(repo), path)
    assert ledger.load(path)[f["id"]]["status"] == "deferred"


def test_escalation_is_reported_once_not_on_every_later_stop(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    for _ in range(MAX_BLOCKS + 1):
        stop_decision(str(repo), path)
    later = stop_decision(str(repo), path)
    assert later.block is False
    assert later.escalated == []


def test_cap_survives_the_finding_being_seen_again_by_an_edit(tmp_path):
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    for _ in range(MAX_BLOCKS):
        assert stop_decision(str(repo), path).block is True
        ledger.record(path, _menus(repo))  # the agent edits the file again
    assert stop_decision(str(repo), path).block is False


def test_unchanged_finding_is_not_rewritten_on_every_stop(tmp_path):
    repo, path = _setup(tmp_path)
    (repo / "a.ts").write_text(SLUG.format(name="toSlug"))
    (repo / "b.ts").write_text(SLUG.format(name="makeSlug"))
    ledger.record(path, _finding(("b.ts", "makeSlug"), [("a.ts", "toSlug")]))
    stop_decision(str(repo), path)
    rows = path.read_text().count("\n")
    stop_decision(str(repo), path)
    assert path.read_text().count("\n") == rows


def test_shrunk_finding_is_re_judged(tmp_path):
    # One of three copies removed: 2 sites left -> no longer EXTRACT.
    repo, path = _setup(tmp_path)
    f = _menus(repo)
    ledger.record(path, f)
    (repo / "SortMenu.ts").write_text("export const SortMenu = 1;\n")
    d = stop_decision(str(repo), path)
    row = ledger.load(path)[f["id"]]
    assert row["sites"] == 2
    assert row["verdict"] == NOTE
    assert d.block is False
