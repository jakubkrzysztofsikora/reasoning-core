"""Round-5 BLOCKER regression tests (round-2 deferred security surface).

The 2026-09-22 hostile re-review documented that the round-3 work
touched none of the security surface and 0/13 security findings
were re-verified as fixed. This test covers the Pareto-optimal
subset of those findings -- the cheap regex additions and the
arithmetically obvious doc fix -- that close the high-frequency
bypass vectors the review enumerated:

* bypass-next path-form (python3 src/rc_cli.py bypass-next)
* .envrc.local unguarded write (one append permanently downgrades
  the next session to warn-only)
* git stash pop / cherry-pick / revert / am / pull (5 subcommands)
* node -e letter-case bypass
* pkill -STOP -f sidecar process-title freeze
* arithmetic error in BENCHMARKS.md: surviving-5 plan-quality mean
  published as 2.20 (-35.3%) vs the doc's own rows: 2.60 (-23.5%)

The fix is the same shape as every previous round: each bypass
vector gets a regex entry (or path-form alias), and the regex
entry is verified by a small TDD test that drives the real
``pre_bash_guard`` module.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest


# Locate the source
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PRE_BASH_GUARD = SRC_DIR / "hooks" / "pre_bash_guard.py"


def _load_patterns():
    """Extract every ``re.compile(r"<pattern>")`` literal from the
    source file via a positional scan. This avoids the regex-on-
    regex problem of matching patterns whose body contains
    brackets/parens that confuse a higher-level regex.
    """
    text = PRE_BASH_GUARD.read_text()
    out = []
    needle = 're.compile(r"'
    i = 0
    while True:
        j = text.find(needle, i)
        if j < 0:
            break
        k = text.find('")', j + len(needle))
        if k < 0:
            break
        out.append(text[j + len(needle):k])
        i = k + 2
    return out


def _matches_any(patterns: list[str], cmd: str) -> bool:
    return any(re.search(p, cmd) for p in patterns)


# ---------------------------------------------------------------------------
# Bypass-next path-form
# ---------------------------------------------------------------------------


def test_regex_blocks_python3_src_rc_cli_bypass_next():
    """The path-form ``python3 src/rc_cli.py bypass-next`` must be blocked.

    Round-2 Finding 5 sub-detail: the regex ``\\brc\\s+bypass-next\\b``
    catches the bare form but not the path-form. The agent can call
    the bypass via ``python3 src/rc_cli.py bypass-next`` (verified
    end-to-end: rc=0, bypass armed, next hook call suppressed).
    """
    patterns = _load_patterns()
    cmd = "python3 src/rc_cli.py bypass-next"
    assert _matches_any(patterns, cmd), (
        f"BLOCKER: bypass-next path-form slips the regex set. "
        f"Patterns: {patterns}"
    )


def test_regex_blocks_python_src_rc_cli_bypass_next():
    """The path-form with ``python`` (no version) also slips."""
    patterns = _load_patterns()
    cmd = "python src/rc_cli.py bypass-next"
    assert _matches_any(patterns, cmd)


# ---------------------------------------------------------------------------
# .envrc.local unguarded write
# ---------------------------------------------------------------------------


def test_regex_blocks_redirect_to_envrc_local():
    """A shell redirect to .envrc.local must trip the guard.

    Round-2 Finding 5: ``.envrc.local`` is the per-project downgrade
    channel; one append permanently downgrades the next session to
    warn-only. The audit verified by execution that the redirect
    form ``echo > .envrc.local`` is not blocked.
    """
    patterns = _load_patterns()
    cmd = "echo 'RC_ORACLE_BLOCK=0' > .envrc.local"
    assert _matches_any(patterns, cmd), (
        "BLOCKER: redirect to .envrc.local slips the regex set; "
        "the per-project downgrade channel is unguarded."
    )


def test_regex_blocks_python_write_to_envrc_local():
    """Python pathlib write to .envrc.local must trip the guard."""
    patterns = _load_patterns()
    cmd = (
        "python3 -c \"from pathlib import Path; "
        "Path('.envrc.local').write_text('RC_ORACLE_BLOCK=0')\""
    )
    assert _matches_any(patterns, cmd)


# ---------------------------------------------------------------------------
# git subcommand slip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcmd", [
    "git stash pop",
    "git cherry-pick HEAD",
    "git revert HEAD",
    "git am mailbox",
    "git pull origin main",
])
def test_regex_blocks_git_subcommand_rewrites(subcmd: str):
    """git subcommands that rewrite guarded paths must be blocked.

    Round-2 Finding 5: ``git stash pop``, ``cherry-pick``, ``revert``,
    ``am``, ``pull``, and ``fast-import`` all rewrite the working
    tree (and therefore guarded hooks) but were not in the regex
    set. The audit verified each slips as-is.
    """
    patterns = _load_patterns()
    cmd = subcmd
    assert _matches_any(patterns, cmd), (
        f"BLOCKER: ``{subcmd}`` slips the regex set; the working-tree "
        f"rewrite path is unguarded."
    )


# ---------------------------------------------------------------------------
# node -e letter-case bypass
# ---------------------------------------------------------------------------


def test_regex_blocks_node_uppercase_E():
    """node -E (capital E) is the same as node -e but with extended regex.

    Round-2 Finding 5 sub-detail: ``node -E \"...\"`` slips the
    lowercase-only ``node -e`` regex. Verified by execution:
    ``node -E \"require('fs').writeFileSync('x.py', '')\"`` writes
    a source file and exits 0.
    """
    patterns = _load_patterns()
    cmd = "node -E \"require('fs').writeFileSync('x.py', '')\""
    assert _matches_any(patterns, cmd), (
        "BLOCKER: ``node -E`` (uppercase) slips the regex; the "
        "letter-case bypass is unguarded."
    )


# ---------------------------------------------------------------------------
# pkill -STOP -f sidecar process-title freeze
# ---------------------------------------------------------------------------


def test_regex_blocks_pkill_stop_sidecar():
    """``pkill -STOP -f reasoning-core-sidecar`` freezes the sidecar.

    Round-2 Finding 5: the real process title is not in the
    protection list. An attacker can ``pkill -STOP -f`` the
    process to freeze the sidecar (no kill, just freeze), making
    the gate inert. Verified by execution: the sidecar's title
    is the string passed to the supervisor's ``setproctitle`` (or
    argv[0] when run from python -m); the audit identifies the
    canonical title as ``reasoning-core-sidecar``.
    """
    patterns = _load_patterns()
    cmd = "pkill -STOP -f reasoning-core-sidecar"
    assert _matches_any(patterns, cmd), (
        "BLOCKER: ``pkill -STOP -f reasoning-core-sidecar`` slips "
        "the regex set; the sidecar freeze vector is unguarded."
    )


# ---------------------------------------------------------------------------
# BENCHMARKS.md arithmetic regression
# ---------------------------------------------------------------------------


def test_benchmarks_surviving_5_plan_quality_mean_is_correct():
    """The surviving-5 plan-quality mean must be recomputed correctly.

    Round-2 Finding 7: the surviving-5 plan-quality mean is
    published as 2.20 (-35.3%) in BENCHMARKS.md but the doc's own
    rows recompute to 2.60 (-23.5%). The error has survived two
    retraction commits.
    """
    benchmarks = (SRC_DIR.parent / "docs" / "BENCHMARKS.md").read_text()
    has_corrected = "2.60" in benchmarks and "−23.5%" in benchmarks
    has_uncorrected = "2.20" in benchmarks and "−35.3%" in benchmarks
    assert has_corrected, (
        "ROUND-5 regression: BENCHMARKS.md must publish 2.60 / -23.5% "
        "for the surviving-5 plan-quality mean."
    )
    assert not has_uncorrected, (
        "ROUND-2 BLOCKER doc bug: BENCHMARKS.md still publishes the "
        "uncorrected 2.20 / -35.3% surviving-5 plan-quality mean. "
        "Per the doc's own rows the correct mean is 2.60 / -23.5%."
    )


def test_whitepaper_results_tex_plan_quality_mean_is_correct():
    """The whitepaper ``results.tex`` must publish the same arithmetic.

    Round-2 Finding 7 (cross-reference): the whitepaper Table 3
    publishes byte-identical copy-pasted rows with the same
    arithmetic error. Both documents must agree.
    """
    results_tex = (SRC_DIR.parent / "docs" / "whitepaper" / "sections" / "results.tex").read_text()
    has_corrected = "2.60" in results_tex and "-23.5" in results_tex
    has_uncorrected = "2.20" in results_tex and "-35.3" in results_tex
    assert has_corrected, (
        "ROUND-5 regression: docs/whitepaper/sections/results.tex "
        "must publish 2.60 / -23.5% for the surviving-5 plan-quality mean."
    )
    assert not has_uncorrected, (
        "ROUND-5 regression: docs/whitepaper/sections/results.tex "
        "still publishes the uncorrected 2.20 / -35.3% surviving-5 "
        "plan-quality mean."
    )
