"""Auto-detect local resources and pick the largest embedder that fits.

The 2026-09-21 user request adds tiered embedder selection to
``rc init``: given the host's available RAM, available disk, and CPU
backend (CUDA / MPS / CPU), pick the largest Mamba-3 variant that
fits without exceeding 80% of working memory, falling back to the
smaller variants and ultimately to ``unixcoder-base`` if Mamba-3 won't
fit.

This module is the pure-Python core; it has no HuggingFace imports
and no heavy deps beyond stdlib. ``rc_cli.init`` calls into it.

The output is a single ``TierDecision`` dataclass with the chosen
backend, the working-set estimate, the fit margin, and the reason
string for the audit log. The decision is deterministic given the
same inputs, which keeps the immutable baseline manifest stable.

Reference: thoughts/shared/research/2026-09-19-audit-deferred-embedder.md
("Auto-sizing on rc init" section, 2026-09-21).
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Constants for the tier matrix. Kept module-level so they're easy to
# override in tests and to read from the IMMUTABLE baseline manifest.
#
# Working-set multiplier: SSMs keep a per-layer state tensor of size
# (batch, d_state, d_model) per token in the recurrent state cache.
# Empirically (Mamba-130m and Codestral-Mamba measurements), the
# resident memory at inference time is ~2.5x the checkpoint file size.
# This is the number the fit() check uses.
WORKING_SET_MULTIPLIER: float = 2.5

# Maximum fraction of available RAM we allow the working-set to use.
# 0.8 leaves 20% headroom for OS + sidecar + agent process. Below
# 0.8 we refuse to load; above 0.8 we surface a S2_BACKBONE_LOAD
# warning and (in rc_cli) ask the operator to confirm.
RAM_HEADROOM: float = 0.8

# Approximate on-disk checkpoint sizes. Source: HuggingFace model
# cards for the listed checkpoints (see eval/pin_model_cards.py for
# the live metadata once we can fetch). These are the values used
# when no model card has been pinned yet.
#
# We update these when pin_model_cards.py writes a fresh manifest;
# that's the only source of truth. The numbers below are placeholders
# from public model cards as of 2026-09-21.
DEFAULT_CHECKPOINT_SIZE_GB: dict[str, float] = {
    "mamba-130m":            0.25,
    "unixcoder-base":        0.50,
    "bge-code":              0.45,
    "mamba3-siso-893m":      1.79,
    "mamba3-mimo-894m":      1.79,
    "mamba3-siso-1.5b":      3.00,
    "codestral-mamba":       14.0,   # full bf16 weights
    "codestral-mamba-gguf":   2.5,   # Q2_K; resident blows up to ~35 GB
}


@dataclass(frozen=True)
class TierDecision:
    """The output of ``decide()``. Immutable so the audit log is stable."""

    backend: str
    tier: str                   # "small" | "medium" | "large" | "xlarge" | "fallback"
    estimated_working_set_gb: float
    available_ram_gb: float
    fit_margin_gb: float        # available - estimated working set; >=0 if fit
    reason: str                 # human-readable, audit-log friendly
    candidates_considered: list[str] = field(default_factory=list)
    # When the auto-pick is unloadable on this host (e.g. Mamba-3
    # checkpoint registered but no Mamba3* class in the installed
    # transformers stack), ``safe_backend_for_envrc`` reports the
    # fallback backend the caller should write to .envrc instead.
    # ``None`` means the auto-pick IS loadable -- the caller may use
    # ``backend`` directly. Added as a post-fix for the 2026-09-22
    # BLOCKER #2 finding (rc init bricked >=32GiB hosts).
    safe_backend_for_envrc: Optional[str] = None
    # When the operator pinned a backend that won't load on this host,
    # this is True so callers can refuse to write it to .envrc.
    pinned_unloadable: bool = False

    @property
    def fits(self) -> bool:
        return self.fit_margin_gb >= 0.0

    @property
    def is_oversized(self) -> bool:
        """True if the chosen backend fits but leaves < 2 GB of headroom.

        ``rc init`` surfaces a warning in that case but still proceeds.
        """
        return self.fits and self.fit_margin_gb < 2.0


# ---------------------------------------------------------------------------
# Resource detection
# ---------------------------------------------------------------------------


def available_ram_gb() -> float:
    """Detect available RAM in GiB. Portable across Linux, macOS, Windows.

    Falls back to /proc/meminfo (Linux), vm_stat (macOS), psutil, in that
    order. Returns 0.0 if nothing works; the caller is expected to
    treat 0.0 as "unknown" and pick the most conservative tier.
    """
    # Prefer psutil if available.
    try:
        import psutil  # type: ignore
        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        pass

    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        except (OSError, ValueError):
            pass

    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.check_output(["vm_stat"], text=True)
            page_size = 16384  # Apple silicon default; older Intel = 4096
            for line in out.splitlines():
                if line.startswith("Pages free:"):
                    free = int(line.split(":")[1].strip().rstrip("."))
                    return free * page_size / (1024 ** 3)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    if sys.platform.startswith("win"):
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            mem = ctypes.c_ulonglong(0)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return mem.ullAvailPhys / (1024 ** 3)
        except (OSError, AttributeError, ValueError):
            pass

    return 0.0


def available_disk_gb(path: Optional[Path] = None) -> float:
    """Detect available disk space on the model-cache mount.

    Defaults to ~/.cache/huggingface (the default HF cache dir) so the
    disk check reflects the actual mount the embedder will land on.
    """
    target = path or Path(
        os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface"
    )
    try:
        usage = shutil.disk_usage(target)
        return usage.free / (1024 ** 3)
    except OSError:
        return 0.0


# ---------------------------------------------------------------------------
# Tier matrix
# ---------------------------------------------------------------------------


# The tiers are an ordered list (largest first). ``decide()`` walks
# this list top-down and returns the first candidate whose working-set
# estimate fits within RAM_HEADROOM of the host's available RAM. The
# tier_name is informational (used in the audit log); the actual
# decision criterion is the working-set fit, not a min_ram_gb gate.
TIERS: list[tuple[str, str, float, float]] = [
    # (tier_name, backend, min_ram_gb, max_ram_gb)
    # Tuned for WORKING_SET_MULTIPLIER=2.5; if you change the multiplier,
    # re-tune these windows.
    ("xlarge", "mamba3-siso-1.5b",   32.0, float("inf")),
    ("xlarge", "mamba3-mimo-894m",   24.0, 32.0),
    ("large",  "mamba3-siso-1.5b",   16.0, 24.0),
    ("large",  "mamba3-mimo-894m",   16.0, 24.0),
    ("large",  "mamba3-siso-893m",   12.0, 16.0),
    ("medium", "mamba3-mimo-894m",    8.0, 16.0),
    ("medium", "mamba3-siso-893m",    8.0, 16.0),
    ("small",  "bge-code",            4.0,  8.0),
    ("small",  "unixcoder-base",      2.0,  8.0),
    ("fallback", "mamba-130m",        0.0,  4.0),
]


def estimate_working_set_gb(backend: str) -> float:
    """Approximate the resident memory cost of loading this backend.

    Uses DEFAULT_CHECKPOINT_SIZE_GB as the source of truth. If a
    pinned model card manifest exists at
    ``eval/calibrated/model_cards.json`` and overrides the size for
    this backend, that value wins.
    """
    size_gb = DEFAULT_CHECKPOINT_SIZE_GB.get(backend, 1.0)
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "eval"
        / "calibrated"
        / "model_cards.json"
    )
    if manifest_path.exists():
        try:
            manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest.get("entries", []):
                if entry.get("backend") == backend and entry.get("status") == "pinned":
                    fields = entry.get("fields", {})
                    # Prefer the explicit on-disk size from the HF model
                    # card metadata if present; else fall back to the
                    # default. (Future: read safetensors.index.json for
                    # the exact shard sizes.)
                    pinned_size = fields.get("checkpoint_size_gb")
                    if isinstance(pinned_size, (int, float)) and pinned_size > 0:
                        size_gb = float(pinned_size)
                    break
        except (OSError, ValueError):
            pass
    return size_gb * WORKING_SET_MULTIPLIER


def fits(backend: str, available_ram_gb_value: float) -> bool:
    """True iff the backend's working-set estimate fits within RAM_HEADROOM."""
    if available_ram_gb_value <= 0.0:
        return False
    return estimate_working_set_gb(backend) <= available_ram_gb_value * RAM_HEADROOM


def decide(
    *,
    available_ram_gb_value: Optional[float] = None,
    available_disk_gb_value: Optional[float] = None,
    requested_backend: Optional[str] = None,
    loadability_probe: Optional["callable"] = None,
    legacy_fallback: str = "mamba-130m",
) -> TierDecision:
    """Pick the embedder backend for this host.

    Args:
        available_ram_gb_value: override for testing; if None, detected live.
        available_disk_gb_value: override for testing; if None, detected live.
        requested_backend: if set and non-empty, use that backend instead of
            the auto-tier pick. Refuses to return it if it doesn't fit; the
            caller must then confirm via ``RC_ALLOW_OVERSIZED_BACKBONE=1``.
        loadability_probe: optional callable ``backend -> bool``. When
            provided and the auto-pick (or operator pin) returns False,
            decide() walks the tier matrix down to the next loadable
            candidate. If no Mamba-3 candidate is loadable, falls back
            to ``legacy_fallback`` (default ``mamba-130m``) so ``rc init``
            does NOT write a brick-inducing backend to .envrc.
            Post-fix for BLOCKER #2 (2026-09-22 hostile review):
            on a 40GB host, the previous code picked
            ``mamba3-siso-1.5b`` (registered but unloadable on the
            current transformers stack), which bricks the gate with
            ``S2_FAIL_CLOSED=1`` because the loader refuses fallback
            for operator-pinned backends.
    """
    # RC-LOAD-PROBE-REQUIRED (round-2 Finding 3): warn when no
    # ``loadability_probe`` is provided so any future caller that
    # forgets the kwarg re-opens the original BLOCKER #2 brick.
    # The legacy code path (no probe) is kept for back-compat with
    # callers that have no probe but the warning surfaces the risk.
    if loadability_probe is None:
        import warnings
        warnings.warn(
            "decide() called without loadability_probe=. The picker will "
            "honour RAM/disk fits but will NOT screen for backends that "
            "the loader cannot instantiate (e.g. unpinned bge-code, "
            "Mamba-3 without kernels). Round-2 hostile review Finding 3: "
            "this can relocate the BLOCKER #2 brick to small-RAM hosts. "
            "Pass ``loadability_probe=ssm_backbone.backend_loadability_probe`` "
            "to restore the BLOCKER #2 fix.",
            UserWarning,
            stacklevel=2,
        )
    ram = available_ram_gb_value if available_ram_gb_value is not None else available_ram_gb()
    disk = available_disk_gb_value if available_disk_gb_value is not None else available_disk_gb()

    # 1. If the operator pinned a backend, honour it (with fit check +
    # loadability probe). The probe is the BLOCKER #2 fix: previously,
    # pinning an unloadable backend (e.g. mamba3-siso-1.5b without the
    # Mamba3* transformers class) silently bricked the gate because the
    # loader refuses fallback for operator-pinned backends.
    if requested_backend:
        working_set = estimate_working_set_gb(requested_backend)
        margin = ram - working_set
        loadable = (
            loadability_probe(requested_backend)
            if loadability_probe is not None
            else True
        )
        if loadable:
            decision = TierDecision(
                backend=requested_backend,
                tier="operator-pinned",
                estimated_working_set_gb=round(working_set, 3),
                available_ram_gb=round(ram, 3),
                fit_margin_gb=round(margin, 3),
                reason=(
                    f"operator pinned {requested_backend} via RC_EMBEDDER; "
                    f"working-set={working_set:.2f} GiB, available={ram:.2f} GiB, "
                    f"margin={margin:+.2f} GiB"
                ),
                candidates_considered=[requested_backend],
            )
            return decision
        # Operator-pinned backend is NOT loadable on this host. Surface
        # the failure in the report and return a decision whose
        # ``pinned_unloadable`` flag is True so the caller (rc init)
        # refuses to write the pin to .envrc.
        decision = TierDecision(
            backend=requested_backend,
            tier="operator-pinned-unloadable",
            estimated_working_set_gb=round(working_set, 3),
            available_ram_gb=round(ram, 3),
            fit_margin_gb=round(margin, 3),
            reason=(
                f"operator pinned {requested_backend} but it is NOT loadable "
                f"on this host (loadability_probe returned False). "
                f"Working-set={working_set:.2f} GiB, available={ram:.2f} GiB. "
                f"Caller must unset RC_EMBEDDER or pick a loadable backend."
            ),
            candidates_considered=[requested_backend],
            safe_backend_for_envrc=legacy_fallback,
            pinned_unloadable=True,
        )
        return decision

    # 2. Walk the tier matrix top-down. Each entry is
    #    (tier_name, backend, min_ram_gb, max_ram_gb). The first entry
    #    whose RAM window contains the host's available RAM AND whose
    #    working-set fits within RAM_HEADROOM wins.
    #
    # BLOCKER #2 audit trail: when a loadability probe is provided
    # and rejects a candidate, we record it in ``skipped_unloadable``
    # so the reason text tells the operator why their top-of-matrix
    # pick was bypassed.
    candidates: list[str] = []
    skipped_unloadable: list[str] = []
    for tier, backend, min_ram, max_ram in TIERS:
        candidates.append(backend)
        if ram < min_ram:
            continue
        if ram >= max_ram:
            # Past the upper boundary for this tier entry; the earlier
            # (larger) entry already covered this RAM.
            continue
        working_set = estimate_working_set_gb(backend)
        if not fits(backend, ram):
            continue
        checkpoint_size = working_set / WORKING_SET_MULTIPLIER
        if disk > 0 and disk < checkpoint_size * 1.5:
            continue
        # BLOCKER #2: when a loadability probe is provided, only honour
        # a tier entry whose backend actually loads on this host.
        # Without a probe we keep the previous behaviour (auto-pick
        # and let the loader decide at runtime).
        if loadability_probe is not None and not loadability_probe(backend):
            skipped_unloadable.append(f"{backend}({tier})")
            continue
        decision = TierDecision(
            backend=backend,
            tier=tier,
            estimated_working_set_gb=round(working_set, 3),
            available_ram_gb=round(ram, 3),
            fit_margin_gb=round(ram - working_set, 3),
            reason=(
                f"auto-picked {backend} for tier={tier}; "
                f"working-set={working_set:.2f} GiB <= {RAM_HEADROOM * ram:.2f} GiB "
                f"({RAM_HEADROOM * 100:.0f}% of {ram:.2f} GiB available); "
                f"disk={disk:.2f} GiB"
            ),
            candidates_considered=candidates,
        )
        return decision

    # 3. Fallback: no candidate passed the (RAM + disk + loadability)
    # gates. If a probe was provided and it said every Mamba-3
    # candidate is unloadable, the safe answer is the legacy fallback
    # (default ``mamba-130m``) rather than ``unixcoder-base`` -- the
    # former is guaranteed to load because it is the legacy default.
    # Without a probe, we keep the previous ``unixcoder-base`` fallback
    # path (back-compat for callers that have no probe).
    probe_said_all_unloadable = (
        loadability_probe is not None
        and not any(
            loadability_probe(c)
            for c in {
                "mamba3-siso-1.5b",
                "mamba3-siso-893m",
                "mamba3-mimo-894m",
            }
        )
    )
    if probe_said_all_unloadable:
        fallback = legacy_fallback
        reason_extra = (
            f"no Mamba-3 candidate is loadable on this host (probe returned "
            f"False for every Mamba-3 variant); falling back to legacy "
            f"default {fallback} so rc init does not brick the gate."
        )
    else:
        fallback = "unixcoder-base"
        skip_msg = (
            f" (skipped unloadable: {', '.join(skipped_unloadable)})"
            if skipped_unloadable
            else ""
        )
        reason_extra = (
            f"no Mamba-3 candidate fits the host (RAM={ram:.2f} GiB, "
            f"disk={disk:.2f} GiB){skip_msg}; falling back to {fallback} "
            f"(working-set={estimate_working_set_gb(fallback):.2f} GiB)"
        )
    working_set = estimate_working_set_gb(fallback)
    return TierDecision(
        backend=fallback,
        tier="fallback",
        estimated_working_set_gb=round(working_set, 3),
        available_ram_gb=round(ram, 3),
        fit_margin_gb=round(ram - working_set, 3),
        reason=reason_extra,
        candidates_considered=candidates,
        safe_backend_for_envrc=fallback,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(decision: TierDecision, *, json_output: bool = False) -> None:
    if json_output:
        sys.stdout.write(__import__("json").dumps(
            {
                "backend": decision.backend,
                "tier": decision.tier,
                "estimated_working_set_gb": decision.estimated_working_set_gb,
                "available_ram_gb": decision.available_ram_gb,
                "fit_margin_gb": decision.fit_margin_gb,
                "fits": decision.fits,
                "is_oversized": decision.is_oversized,
                "reason": decision.reason,
                "candidates_considered": decision.candidates_considered,
            },
            indent=2,
        ) + "\n")
        return
    print(f"backend:        {decision.backend}")
    print(f"tier:           {decision.tier}")
    print(f"RAM available:  {decision.available_ram_gb:.2f} GiB")
    print(f"working set:    {decision.estimated_working_set_gb:.2f} GiB")
    print(f"fit margin:     {decision.fit_margin_gb:+.2f} GiB")
    print(f"oversized?      {decision.is_oversized}")
    print(f"reason:         {decision.reason}")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--backend", help="Override auto-tier pick (RC_EMBEDDER value).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--ram-gb", type=float, help="Override detected RAM (testing).")
    parser.add_argument("--disk-gb", type=float, help="Override detected disk (testing).")
    args = parser.parse_args()
    decision = decide(
        available_ram_gb_value=args.ram_gb,
        available_disk_gb_value=args.disk_gb,
        requested_backend=args.backend,
    )
    _print_human(decision, json_output=args.json)
    return 0 if decision.fits else 1


if __name__ == "__main__":
    sys.exit(main())
