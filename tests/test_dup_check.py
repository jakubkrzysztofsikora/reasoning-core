"""Decision logic over confirmed duplicate hits (src/duplicate_detection/check.py).

Pure: hits are built by hand, no index, no embedder, no git.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.duplicate_detection.check import split_moves  # noqa: E402
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
