"""P2 plan-quality CGS tests."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src" / "hooks"))


def test_setup_b_iter1_generic_plan_is_rejected():
    """Setup B's iter-1 plan ('Read full diff once. Re-read by file.') → CGS=0.0."""
    import _plan_quality as pq
    plan = """
    Read the diff. Re-read by file. Verify it works.
    Make sure tests pass. Check edge cases.
    """
    r = pq.composite_gate_score(plan)
    assert r.cgs <= 0.25
    assert r.decision == "reject"


def test_setup_a_iter1_specific_plan_is_accepted():
    """Plan with files, endpoints, named risks, line refs → CGS>=0.75."""
    import _plan_quality as pq
    plan = """
    Phase 1: src/auth/login.ts:42 — verify the rate-limit middleware is applied.
    The /api/auth/login endpoint currently allows N+1 queries via line 87.
    Phase 2: src/api/users.ts — fix race condition between session refresh
    and JWT renewal. CSRF token rotation at /api/csrf line 23 is missing.
    Phase 3: tests/api/auth.spec.ts:120 — add coverage for the unbounded
    pagination case at /api/users/list.
    """
    r = pq.composite_gate_score(plan)
    assert r.cgs >= 0.75, f"got {r.cgs}, signals ard={r.ard:.2f} nrd={r.nrd:.2f} gpas={r.gpas:.2f} slr={r.slr:.2f}"
    assert r.decision == "accept"


def test_borderline_plan_lands_in_warn_band():
    import _plan_quality as pq
    # Has artifacts but generic phrasing — falls in 0.5–0.75 band
    plan = """
    Phase 1: src/auth/login.ts — read the diff and verify it works.
    Phase 2: tests/auth.spec.ts:42 — make sure tests pass.
    """
    r = pq.composite_gate_score(plan)
    # ARD high, SLR high, NRD = 0 (no named risks), GPAS high (3 generic phrases)
    assert r.decision in ("warn", "reject")


def test_empty_plan_rejected():
    import _plan_quality as pq
    r = pq.composite_gate_score("")
    assert r.cgs == 0.0
    assert r.decision == "reject"


def test_ard_counts_files_endpoints_lines():
    import _plan_quality as pq
    # 1 file ref + 1 endpoint + 1 line ref over 1 sentence → 3.0
    assert pq.ard("Edit src/x.ts at /api/users line 42.") >= 1.0


def test_nrd_lexicon_hits():
    import _plan_quality as pq
    plan = "Watch for N+1 queries. Avoid SQL injection. Beware race conditions."
    r = pq.nrd(plan)
    assert r > 0.5  # 3 named risks across ~3 sentences


def test_gpas_blocklist_hits():
    import _plan_quality as pq
    plan = "Read the diff. Verify it works. Check edge cases."
    g = pq.gpas(plan)
    assert g >= 0.5  # multiple blocklist hits

    plan_clean = "Implement /api/users at src/api.ts line 42."
    assert pq.gpas(plan_clean) == 0.0


def test_skip_quality_magic_comment_bypasses_check():
    """# rc:skip-quality at top of plan must bypass _check_specificity."""
    import os, sys, importlib
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    os.environ["RC_PLAN_QUALITY"] = "1"
    try:
        import pre_plan_guard as ppg
        importlib.reload(ppg)
        # Generic plan that would normally fail CGS
        plan_blocked = "Read the diff. Verify it works."
        plan_skipped = "# rc:skip-quality\nRead the diff. Verify it works."
        ws_blocked = ppg._check_specificity(plan_blocked)
        ws_skipped = ppg._check_specificity(plan_skipped)
        assert any(w["rule_id"] == "plan_specificity" for w in ws_blocked)
        assert ws_skipped == [], f"expected magic-comment skip, got {ws_skipped}"
    finally:
        os.environ.pop("RC_PLAN_QUALITY", None)


def test_plan_quality_disabled_when_flag_unset():
    import os, sys, importlib
    sys.path.insert(0, str(_ROOT / "src" / "hooks"))
    os.environ.pop("RC_PLAN_QUALITY", None)
    import pre_plan_guard as ppg
    importlib.reload(ppg)
    assert ppg._check_specificity("Read the diff. Verify it works.") == []


def test_slr_specificity_to_length():
    import _plan_quality as pq
    plan = """
    Phase 1: edit src/foo.ts.
    Make sure it works.
    Phase 2: edit src/bar.ts.
    Verify everything.
    """
    # 2 of 4 sentences have artifacts → SLR ~0.5
    r = pq.slr(plan)
    assert 0.4 <= r <= 0.6
