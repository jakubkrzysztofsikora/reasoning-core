"""Decision logic over confirmed duplicate hits (src/duplicate_detection/check.py).

Pure: hits are built by hand, no index, no embedder, no git.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection.check import (  # noqa: E402
    EXTRACT,
    NOTE,
    load_test_texts,
    site_is_covered,
    split_moves,
    verdict,
)
from src.duplicate_detection.oracle import DupHit  # noqa: E402


def _hit(path: str, name: str) -> DupHit:
    return DupHit(path=path, name=name, lineno=9, cosine=0.99, logic_ratio=1.0, shared=[])


class TestSplitMoves:
    def test_match_in_modified_file_is_a_likely_move(self):
        # initialsOf: lifted out of Avatar.tsx (modified) into a new initials.ts.
        hits = [_hit("src/Avatar.tsx", "initialsOf")]
        real, moves = split_moves(hits, changed={"src/Avatar.tsx", "src/lib/initials.ts"})
        assert real == []
        assert moves == hits

    def test_match_in_untouched_file_is_real(self):
        # onDocClick: the other menus are not part of this change.
        hits = [_hit("src/BasemapControl.tsx", "onDocClick"), _hit("src/SortMenu.tsx", "onDocClick")]
        real, moves = split_moves(hits, changed={"src/AccountMenu.tsx"})
        assert real == hits
        assert moves == []

    def test_mixed_hits_are_split(self):
        move = _hit("src/ConnectStrava.tsx", "clearConnectParam")
        dup = _hit("src/Other.tsx", "clearParam")
        real, moves = split_moves([move, dup], changed={"src/ConnectStrava.tsx"})
        assert real == [dup]
        assert moves == [move]

    def test_unknown_diff_treats_every_hit_as_real(self):
        # Safe default: when git cannot tell us what changed, report, don't hide.
        hits = [_hit("src/Avatar.tsx", "initialsOf")]
        real, moves = split_moves(hits, changed=None)
        assert real == hits
        assert moves == []


class TestCoverage:
    def _repo(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "AccountMenu.tsx").write_text("export function AccountMenu() {}")
        (tmp_path / "src" / "SortMenu.tsx").write_text("export function SortMenu() {}")
        (tmp_path / "src" / "AccountMenu.test.tsx").write_text("import { AccountMenu } from './AccountMenu'")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "x.test.js").write_text("SortMenu")
        return tmp_path

    def test_finds_test_files_and_skips_vendored(self, tmp_path):
        texts = load_test_texts(str(self._repo(tmp_path)))
        assert list(texts) == ["src/AccountMenu.test.tsx"]

    def test_site_covered_when_a_test_mentions_its_file(self, tmp_path):
        texts = load_test_texts(str(self._repo(tmp_path)))
        assert site_is_covered("src/AccountMenu.tsx", "onDocClick", texts)
        assert not site_is_covered("src/SortMenu.tsx", "onDocClick", texts)

    def test_site_covered_when_a_test_mentions_the_function(self):
        texts = {"tests/test_x.py": "from x import onDocClick"}
        assert site_is_covered("src/other.py", "onDocClick", texts)

    def test_a_site_inside_a_test_file_counts_as_covered(self):
        assert site_is_covered("src/App.test.tsx", "renderApp", {})


class TestVerdict:
    def test_three_covered_nontrivial_sites_must_be_extracted(self):
        # onDocClick with Escape handling: 23 logic tokens, 3 menus, tested.
        assert verdict(sites=3, n_tokens=23, covered=True) == EXTRACT

    def test_trivial_helper_is_note_only(self):
        # setUrl: a 3-line replaceState wrapper (4 tokens) in 3 test files.
        assert verdict(sites=3, n_tokens=4, covered=True) == NOTE
        # renderApp: 8 tokens.
        assert verdict(sites=3, n_tokens=8, covered=True) == NOTE

    def test_two_sites_is_note_only(self):
        assert verdict(sites=2, n_tokens=23, covered=True) == NOTE

    def test_uncovered_sites_are_note_only(self):
        assert verdict(sites=3, n_tokens=23, covered=False) == NOTE
