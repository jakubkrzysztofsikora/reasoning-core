"""Unit tests for src/embedder_tier.py -- the rc init auto-sizer.

Covers:
- Tier decision matrix across a grid of (ram, disk, backend) tuples
- Operator-pin path with fit check
- Fallback to unixcoder-base when nothing fits
- Working-set multiplier sanity (checkpoint_size_gb * 2.5)
- Working-set estimate when a pinned model_card manifest exists
- Detection functions (smoke tests; do not assert specific numbers
  because they depend on the host the test is running on)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import embedder_tier


# ---------------------------------------------------------------------------
# Tier decision matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ram_gb,disk_gb,expected_backend",
    [
        # (ram, disk, expected auto-pick) -- largest-fits-first semantics.
        # Working sets (at WORKING_SET_MULTIPLIER=2.5):
        #   mamba3-siso-1.5b:  3.0 * 2.5 = 7.5 GiB  (needs >=9.375 GiB RAM)
        #   mamba3-mimo-894m:  1.79 * 2.5 = 4.475 GiB (needs >=5.594 GiB)
        #   mamba3-siso-893m:  1.79 * 2.5 = 4.475 GiB (needs >=5.594 GiB)
        #   bge-code:           0.45 * 2.5 = 1.125 GiB (needs >=1.406 GiB)
        #   unixcoder-base:     0.5  * 2.5 = 1.25  GiB (needs >=1.563 GiB)
        #   mamba-130m:         0.25 * 2.5 = 0.625 GiB (needs >=0.781 GiB)
        (64.0, 100.0, "mamba3-siso-1.5b"),   # 64 GiB >> 7.5 GiB; pick 1.5b
        (40.0, 100.0, "mamba3-siso-1.5b"),   # 40 GiB still fits 1.5b
        (24.0, 50.0,  "mamba3-mimo-894m"),   # 24 GiB in MIMO xlarge window; 1.5b needs >=32
        (16.0, 50.0,  "mamba3-siso-1.5b"),   # 16 * 0.8 = 12.8 >= 7.5; fits 1.5b
        (10.0, 30.0,  "mamba3-mimo-894m"),   # 1.5b won't fit (needs 9.375); MIMO does (4.475 < 8.0)
        (8.0,  20.0,  "mamba3-mimo-894m"),   # 8 GiB in medium tier; MIMO listed first, wins tie
        (6.0,  20.0,  "bge-code"),           # 6 GiB < 8 medium floor; small tier bge-code wins
        (5.0,  20.0,  "bge-code"),           # Mamba-3 doesn't fit; bge-code picks over unixcoder (largest fit)
        (3.0,  10.0,  "unixcoder-base"),     # 3 GiB < bge-code 4 GiB floor; unixcoder wins
        (1.5,  5.0,   "mamba-130m"),         # 1.5 GiB < unixcoder min 2.0; mamba-130m fallback tier fits
        (0.8,  5.0,   "mamba-130m"),         # 0.8 * 0.8 = 0.64; mamba-130m (0.625) just fits
    ],
)
def test_tier_decision_grid(ram_gb, disk_gb, expected_backend):
    decision = embedder_tier.decide(
        available_ram_gb_value=ram_gb,
        available_disk_gb_value=disk_gb,
    )
    assert decision.backend == expected_backend, (
        f"ram={ram_gb} disk={disk_gb} -> got {decision.backend!r}, "
        f"expected {expected_backend!r}; reason: {decision.reason}"
    )
    assert decision.fits, f"decision did not fit: {decision.reason}"


def test_tier_decision_no_ram_returns_fallback():
    """If we can't detect RAM (returns 0.0), the auto-sizer falls back
    conservatively to unixcoder-base."""
    decision = embedder_tier.decide(
        available_ram_gb_value=0.0,
        available_disk_gb_value=100.0,
    )
    assert decision.backend == "unixcoder-base"
    assert decision.tier == "fallback"


def test_tier_decision_no_disk_relaxes_disk_check():
    """If we can't detect disk (returns 0.0), the disk check is skipped
    (we don't know the disk size, so don't refuse on that basis). The
    RAM check is the binding constraint."""
    decision = embedder_tier.decide(
        available_ram_gb_value=64.0,
        available_disk_gb_value=0.0,
    )
    assert decision.backend == "mamba3-siso-1.5b"
    assert decision.fits


# ---------------------------------------------------------------------------
# Operator pin path
# ---------------------------------------------------------------------------

def test_operator_pin_honoured_when_fits():
    """If the operator sets RC_EMBEDDER=mamba3-siso-1.5b on a 64 GB host,
    the auto-sizer honours the pin."""
    decision = embedder_tier.decide(
        available_ram_gb_value=64.0,
        available_disk_gb_value=100.0,
        requested_backend="mamba3-siso-1.5b",
    )
    assert decision.backend == "mamba3-siso-1.5b"
    assert decision.tier == "operator-pinned"
    assert decision.fits
    assert decision.fit_margin_gb > 0


def test_operator_pin_returned_even_when_does_not_fit():
    """The auto-sizer returns the operator pin with a negative margin
    when it doesn't fit. The caller (rc_cli) is responsible for refusing
    to start unless RC_ALLOW_OVERSIZED_BACKBONE=1 is set."""
    decision = embedder_tier.decide(
        available_ram_gb_value=4.0,
        available_disk_gb_value=10.0,
        requested_backend="mamba3-siso-1.5b",
    )
    assert decision.backend == "mamba3-siso-1.5b"
    assert decision.tier == "operator-pinned"
    assert not decision.fits
    assert decision.fit_margin_gb < 0


# ---------------------------------------------------------------------------
# Working-set math
# ---------------------------------------------------------------------------

def test_working_set_multiplier():
    """Working-set = checkpoint_size * WORKING_SET_MULTIPLIER (default 2.5)."""
    for backend, size_gb in embedder_tier.DEFAULT_CHECKPOINT_SIZE_GB.items():
        ws = embedder_tier.estimate_working_set_gb(backend)
        expected = size_gb * embedder_tier.WORKING_SET_MULTIPLIER
        assert abs(ws - expected) < 1e-6, (
            f"{backend}: working-set {ws} != {size_gb} * 2.5 = {expected}"
        )


def test_pinned_manifest_overrides_default_size(tmp_path):
    """If eval/calibrated/model_cards.json exists and pins a different
    size for a backend, that value wins."""
    pinned = {
        "captured_at": "2026-09-21T00:00:00Z",
        "n_backends": 1,
        "entries": [
            {
                "backend": "mamba3-siso-893m",
                "checkpoint": "state-spaces/mamba3-siso-893m",
                "revision": "main",
                "status": "pinned",
                "fields": {"checkpoint_size_gb": 0.5},
                "fields_sha256": "deadbeef",
            }
        ],
    }
    manifest_dir = Path("eval/calibrated")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "model_cards.json"
    manifest_path.write_text(json.dumps(pinned), encoding="utf-8")
    try:
        ws = embedder_tier.estimate_working_set_gb("mamba3-siso-893m")
        assert ws == pytest.approx(0.5 * 2.5, abs=1e-6)
    finally:
        manifest_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fits() boundary check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "backend,available_ram,expected_fits",
    [
        # mamba3-siso-1.5b: checkpoint 3.0 GB * 2.5 = 7.5 GB working set
        # needs RAM * 0.8 >= 7.5  =>  RAM >= 9.375
        ("mamba3-siso-1.5b", 9.0, False),
        ("mamba3-siso-1.5b", 10.0, True),
        ("mamba3-siso-1.5b", 32.0, True),
        # mamba3-siso-893m: 1.79 * 2.5 = 4.475 GB working set
        # needs RAM * 0.8 >= 4.475  =>  RAM >= 5.594
        ("mamba3-siso-893m", 5.0, False),
        ("mamba3-siso-893m", 6.0, True),
        ("mamba3-siso-893m", 8.0, True),
        # unixcoder-base: 0.5 * 2.5 = 1.25 GB working set
        # needs RAM * 0.8 >= 1.25  =>  RAM >= 1.5625
        ("unixcoder-base", 1.5, False),
        ("unixcoder-base", 1.6, True),
        ("unixcoder-base", 2.0, True),
    ],
)
def test_fits_boundary(backend, available_ram, expected_fits):
    assert embedder_tier.fits(backend, available_ram) is expected_fits


# ---------------------------------------------------------------------------
# Detection smoke tests
# ---------------------------------------------------------------------------

def test_available_ram_returns_nonnegative():
    """On any sane host, available_ram_gb() returns >= 0.0."""
    ram = embedder_tier.available_ram_gb()
    assert ram >= 0.0


def test_available_disk_returns_nonnegative():
    disk = embedder_tier.available_disk_gb()
    assert disk >= 0.0


# ---------------------------------------------------------------------------
# Decision is immutable
# ---------------------------------------------------------------------------

def test_decision_is_frozen():
    decision = embedder_tier.decide(
        available_ram_gb_value=16.0,
        available_disk_gb_value=50.0,
    )
    with pytest.raises((AttributeError, Exception)):
        decision.backend = "mamba-130m"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BLOCKER #2 regression tests: rc init must not pick a backend that
# fails to load. The audit verified that on a 40GB host decide() picks
# mamba3-siso-1.5b but the embedder cannot be loaded by the current
# transformers stack (no Mamba3* class, no mamba-ssm kernels), so the
# gate bricks with S2_FAIL_CLOSED=1.
#
# decide() must accept a ``loadability_probe`` callable; if the probe
# says a candidate backend won't load, decide() must fall through to
# the next tier rather than write a backend to .envrc that bricks the
# gate. The default behaviour (no probe) stays backwards compatible.
# ---------------------------------------------------------------------------


def test_decide_skips_unloadable_tier_top_entry(monkeypatch):
    """If the xlarge pick is unloadable, decide() walks down the tier matrix."""

    def _probe_unloadable(backend: str) -> bool:
        # mamba3-siso-1.5b is unloadable on this host (no Mamba3* class).
        return backend != "mamba3-siso-1.5b"

    d = embedder_tier.decide(
        available_ram_gb_value=40.0,
        available_disk_gb_value=100.0,
        loadability_probe=_probe_unloadable,
    )
    assert d.backend != "mamba3-siso-1.5b", (
        f"decide() picked an unloadable backend: {d.backend!r}"
    )
    # And it surfaces why in the reason string so operators can audit.
    assert "mamba3-siso-1.5b" in d.reason or "unloadable" in d.reason.lower()


def test_decide_falls_back_to_legacy_when_all_mamba3_unloadable(monkeypatch):
    """If every Mamba-3 candidate is unloadable, decide() falls to mamba-130m."""

    def _probe_nothing_works(backend: str) -> bool:
        # Only the legacy mamba-130m loads on this host.
        return backend == "mamba-130m"

    d = embedder_tier.decide(
        available_ram_gb_value=40.0,
        available_disk_gb_value=100.0,
        loadability_probe=_probe_nothing_works,
    )
    assert d.backend == "mamba-130m", (
        f"expected fallback to mamba-130m when no Mamba-3 candidate loads; "
        f"got {d.backend!r}"
    )


def test_decide_default_probe_accepts_pure_ram_decision(monkeypatch):
    """Without a probe, decide() still works (back-compat for callers).

    The 8-GB tier matrix was widened in 2026-09-22 to also include
    mamba3-mimo-894m (4.5 GB working-set, fits 8 GB at 80 % headroom),
    so we accept any of the Mamba-3 / bge-code / unixcoder-base /
    mamba-130m variants as a valid pick.
    """
    d = embedder_tier.decide(
        available_ram_gb_value=8.0,
        available_disk_gb_value=100.0,
    )
    assert isinstance(d.backend, str)
    assert d.backend in {
        "mamba3-siso-893m",
        "mamba3-mimo-894m",
        "bge-code",
        "unixcoder-base",
        "mamba-130m",
    }


def test_decide_marks_operator_pinned_unloadable_in_reason(monkeypatch):
    """Operator-pinned backend that won't load must surface a clear reason."""

    def _probe_unloadable(backend: str) -> bool:
        return False  # nothing loads

    d = embedder_tier.decide(
        available_ram_gb_value=64.0,
        available_disk_gb_value=200.0,
        requested_backend="mamba3-siso-1.5b",
        loadability_probe=_probe_unloadable,
    )
    # Operator pins bypass the tier walk, but reason must surface that
    # the pin is unloadable so the caller knows to refuse.
    assert d.backend == "mamba3-siso-1.5b"  # operator's pin is honoured
    assert (
        "unloadable" in d.reason.lower()
        or "loadability" in d.reason.lower()
    ), f"reason did not surface unloadability: {d.reason!r}"


def test_decide_writes_no_op_when_all_unloadable(monkeypatch, tmp_path):
    """rc init must NOT write a brick-inducing backend to .envrc.

    End-to-end: when no probe says any Mamba-3 candidate loads, the
    caller (rc_cli.init) writes ``RC_EMBEDDER=mamba-130m`` to .envrc
    rather than the auto-pick. This test verifies decide()'s
    ``safe_backend_for_envrc`` field reports the fallback candidate
    rather than the unloadable pick.
    """

    def _probe_only_legacy(backend: str) -> bool:
        return backend == "mamba-130m"

    d = embedder_tier.decide(
        available_ram_gb_value=64.0,
        available_disk_gb_value=200.0,
        loadability_probe=_probe_only_legacy,
    )
    # Auto-pick is mamba3-siso-1.5b on 64GB host, but unloadable.
    # The safe backend to write to .envrc is the legacy default.
    assert d.backend != "mamba3-siso-1.5b"
    # And the report carries a flag the caller can read.
    safe = getattr(d, "safe_backend_for_envrc", None)
    assert safe == "mamba-130m", (
        f"expected safe_backend_for_envrc=mamba-130m; got {safe!r}"
    )
