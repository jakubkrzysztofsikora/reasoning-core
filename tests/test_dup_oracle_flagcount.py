"""Unit tests for the statusline flag-counter (src/dup_oracle_flagcount.py).

Pure + offline: the colour gradient and the per-session counter, no torch, no
terminal. The colour semantics are load-bearing (0 = green "all clear", then
pale-orange escalating to red as duplication piles up), so they're asserted on
the actual RGB channels, not just "returns a string".
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from src.dup_oracle_flagcount import (  # noqa: E402
    CAP,
    GREEN,
    PALE_ORANGE,
    RED,
    bump,
    colour_for,
    counter_path,
    read_count,
    render,
)


# --- colour gradient: 0 green -> pale orange -> red -------------------------


def test_zero_is_green_all_clear():
    assert colour_for(0) == GREEN


def test_first_flag_is_pale_orange():
    assert colour_for(1) == PALE_ORANGE


def test_saturates_to_red_at_and_beyond_cap():
    assert colour_for(CAP) == RED
    assert colour_for(CAP + 50) == RED  # clamped, never past red


def test_gradient_moves_toward_red_across_all_channels():
    # As the count climbs 1 -> CAP the colour must march toward red on EVERY
    # channel (PALE_ORANGE -> RED decreases R, G and B). Asserting all three,
    # not just green, kills a mutation that breaks the R- or B-channel
    # interpolation while leaving the endpoints intact.
    ramp = [colour_for(n) for n in range(1, CAP + 1)]
    assert ramp[0] == PALE_ORANGE
    assert ramp[-1] == RED
    for ch in range(3):
        seq = [c[ch] for c in ramp]
        # never moves away from RED...
        assert all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
        # ...and actually moves (a flat/constant interpolation would fail this).
        assert seq[0] > seq[-1]
    # green has the widest span, so pin it as strictly decreasing every step
    greens = [c[1] for c in ramp]
    assert all(greens[i] > greens[i + 1] for i in range(len(greens) - 1))


def test_render_is_truecolor_wrapped_and_matches_colour_for():
    r, g, b = colour_for(3)
    out = render(3)
    assert out.startswith(f"\x1b[38;2;{r};{g};{b}m")
    assert out.endswith("\x1b[0m")


def test_render_pluralises_only_one_flag_as_singular():
    assert "0 flags" in render(0)
    assert "1 flag" in render(1) and "1 flags" not in render(1)
    assert "4 flags" in render(4)


# --- per-session counter ----------------------------------------------------


def test_unknown_session_reads_zero(tmp_path):
    assert read_count("never-seen", base=str(tmp_path)) == 0


def test_bump_increments_and_persists(tmp_path):
    assert bump("s1", base=str(tmp_path)) == 1
    assert bump("s1", base=str(tmp_path)) == 2
    assert read_count("s1", base=str(tmp_path)) == 2


def test_sessions_are_isolated(tmp_path):
    bump("a", base=str(tmp_path))
    bump("a", base=str(tmp_path))
    bump("b", base=str(tmp_path))
    assert read_count("a", base=str(tmp_path)) == 2
    assert read_count("b", base=str(tmp_path)) == 1


def test_bump_is_race_safe_append_not_rewrite(tmp_path):
    # Race-safety comes from appending ONE record per flag rather than
    # read-modify-writing a single number: N bumps must leave N records on disk,
    # so two concurrent appends can never clobber each other's increment. A
    # mutation back to "write str(n)" leaves a single record and fails here.
    for _ in range(3):
        bump("s", base=str(tmp_path))
    raw = Path(counter_path("s", base=str(tmp_path))).read_text()
    records = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(records) == 3  # one record per flag, not the digit "3"
    assert read_count("s", base=str(tmp_path)) == 3


def test_concurrent_bumps_lose_no_increments(tmp_path):
    # Behavioural backstop: many threads bumping the SAME session at once must
    # all be counted. Read-modify-write drops updates here; append does not.
    import threading

    n = 200
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()  # maximise contention: everyone bumps together
        bump("race", base=str(tmp_path))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert read_count("race", base=str(tmp_path)) == n


def test_counter_path_sanitises_session_id_no_traversal(tmp_path):
    # A hostile/odd session_id must not escape the base dir.
    p = Path(counter_path("../../etc/evil", base=str(tmp_path)))
    assert p.parent == Path(str(tmp_path)) / "rc-dup-oracle"
    assert ".." not in p.name


def test_bump_fails_open_returns_zero_on_unwritable_base(tmp_path):
    # base points at a FILE, so mkdir under it can't succeed -> must swallow and
    # return 0, never raise (a statusline counter must never break an edit).
    afile = tmp_path / "not-a-dir"
    afile.write_text("x")
    assert bump("s", base=str(afile / "under")) == 0
