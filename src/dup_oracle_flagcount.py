"""Per-session flag counter + colour gradient for the dup-oracle statusline.

The advisory hook (``src/hooks/pre_edit_dup_advisory.py``) bumps a per-session
tally each time it actually emits a reuse advisory; the statusline
(``src/hooks/dup_oracle_statusline.py``) reads that tally and renders it, so a
developer can see at a glance how often the oracle is catching duplication this
session.

Colour semantics (deliberate): 0 = green ("all clear"), the first flag is pale
orange, and it marches steadily to red as the count climbs toward ``CAP`` -- more
flags means more repetition, so it reads hotter over time. Pure and terminal-free
so it's fully unit-testable.

Everything fails open: a statusline counter must never break an edit.
"""
from __future__ import annotations

import os
import tempfile

# Gradient anchors (R, G, B). 0 flags -> GREEN; 1 -> PALE_ORANGE; CAP+ -> RED.
GREEN = (46, 204, 113)
PALE_ORANGE = (245, 190, 120)
RED = (231, 76, 60)
CAP = 10  # flags at which the colour saturates fully red

_SUBDIR = "rc-dup-oracle"


def colour_for(count: int) -> tuple[int, int, int]:
    """RGB for a flag count: green at 0, then a linear ramp PALE_ORANGE->RED
    over 1..CAP (clamped)."""
    if count <= 0:
        return GREEN
    t = min((count - 1) / (CAP - 1), 1.0)
    return tuple(  # type: ignore[return-value]
        round(PALE_ORANGE[i] + (RED[i] - PALE_ORANGE[i]) * t) for i in range(3)
    )


def render(count: int) -> str:
    """A truecolor-wrapped statusline segment, e.g. ``♻️ 3 dups`` (green at 0)."""
    r, g, b = colour_for(count)
    noun = "dup" if count == 1 else "dups"
    return f"\x1b[38;2;{r};{g};{b}m♻️ {count} {noun}\x1b[0m"


def _base_dir(base: str | None) -> str:
    return os.path.join(base or tempfile.gettempdir(), _SUBDIR)


def counter_path(session_id: str, base: str | None = None) -> str:
    """Path to a session's counter file. The id is sanitised to a bare filename
    so an odd/hostile ``session_id`` can never escape the base directory."""
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_") or "nosession"
    return os.path.join(_base_dir(base), f"{safe}.count")


def read_count(session_id: str, base: str | None = None) -> int:
    """The tally = the number of records (non-blank lines) in the session file.
    One record was appended per flag, so counting them is the count."""
    try:
        with open(counter_path(session_id, base)) as f:
            return sum(1 for line in f if line.strip())
    except Exception:  # noqa: BLE001 - missing/garbage file reads as zero
        return 0


def bump(session_id: str, base: str | None = None) -> int:
    """Record one flag and return the new count (0 on any failure -- never
    raises). We *append* a single record rather than read-modify-writing a
    number: an ``O_APPEND`` write is atomic per record on POSIX, so parallel
    PreToolUse hooks (Claude runs edits concurrently) can't clobber each other's
    increment the way a rewrite would."""
    try:
        path = counter_path(session_id, base)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:  # append mode: one record per flag
            f.write("1\n")
        return read_count(session_id, base)
    except Exception:  # noqa: BLE001 - fail open: a counter must never break an edit
        return 0
