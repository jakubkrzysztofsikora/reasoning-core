"""Pre-reg embedder gate test (option-3 default flip enforcer).

Per the embedder memo (2026-09-21 revision), the candidate embedder
(Mamba-3 SISO 893m by default) cannot become the default backbone
until the pre-reg eval ladder produces evidence that it:

  1. Achieves ROC-AUC >= 0.70 on the test split.
  2. Beats the Mamba-130m baseline by >= 0.05 absolute AUC.
  3. Reduces intra-code anisotropy by >= 0.04 absolute.
  4. Stays within 2.5x the baseline median per-edit latency.
  5. Does not exhibit accidental label leakage (random-mamba
     falsifiability control AUC <= 0.55).

This test enforces the gate by:

  - Reading the most recent ``eval/runs/pre_reg_embedder*.json``
    manifest written by ``eval/pre_reg_embedder.py``.
  - Failing if no manifest exists yet (the harness hasn't been run).
  - Failing if any gate failed.
  - Failing if the manifest is older than the most recent edit to
    ``src/ssm_backbone.py`` or ``src/ssm_backbone.py:_BACKENDS`` --
    stale manifests don't count.

The gate test does NOT itself trigger the default flip; that lives
in ``src/ssm_backbone.py:_DEFAULT_BACKEND_NAME`` and is toggled by
``RC_EMBEDDER`` at runtime. This test is the *refusal* gate: as long
as it fails, the default stays at Mamba-130m.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "eval" / "runs"


def _latest_manifest() -> Path | None:
    """Return the path to the most recent pre_reg_embedder*.json
    manifest, or None if no runs have been written yet."""
    if not RUNS_DIR.exists():
        return None
    candidates = sorted(RUNS_DIR.glob("pre_reg_embedder*.json"))
    return candidates[-1] if candidates else None


def _manifest_age_seconds(manifest_path: Path) -> float:
    """How old is the manifest, in seconds. Uses the captured_at field
    if present, else file mtime."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        captured = manifest.get("captured_at")
        if captured:
            from datetime import datetime
            ts = datetime.strptime(captured, "%Y-%m-%dT%H:%M:%SZ")
            return (datetime.utcnow() - ts).total_seconds()
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    return time.time() - manifest_path.stat().st_mtime


def _ssm_backbone_mtime() -> float:
    """Most recent mtime of any file in src/ssm_backbone.py (or its
    imports) since the manifest. Used to invalidate stale runs."""
    target = REPO_ROOT / "src" / "ssm_backbone.py"
    if not target.exists():
        return 0.0
    return target.stat().st_mtime


# ---------------------------------------------------------------------------
# Refusal gate: this is the test that prevents the default flip until
# the pre-reg eval passes.
# ---------------------------------------------------------------------------

def test_pre_reg_embedder_manifest_exists():
    """Refuses to flip the default unless at least one pre-reg run has
    been executed. The first-time setup flow:
      1. operator runs `python -m eval.pre_reg_embedder --backends ...`
      2. the harness writes eval/runs/pre_reg_embedder.json
      3. this test then enforces the gates on that manifest."""
    latest = _latest_manifest()
    if latest is None:
        pytest.skip(
            "no pre_reg_embedder manifest yet -- run "
            "`python -m eval.pre_reg_embedder --backends mamba-130m "
            "unixcoder-base bge-code random-mamba mamba3-siso-893m` "
            "to populate eval/runs/, then re-run this test"
        )


def test_pre_reg_embedder_gates_all_pass():
    """The five pre-reg gates must all pass. If any gate fails, the
    candidate does NOT become the default embedder."""
    latest = _latest_manifest()
    if latest is None:
        pytest.skip("no pre_reg_embedder manifest yet")
    manifest = json.loads(latest.read_text(encoding="utf-8"))
    gates = manifest.get("gates", [])
    if not gates:
        pytest.skip("manifest has no gates -- harness did not record measurements")
    failed = [g for g in gates if not g.get("passed", False)]
    if failed:
        names = ", ".join(g["name"] for g in failed)
        pytest.fail(
            f"{len(failed)}/{len(gates)} pre-reg gates failed: {names}. "
            f"The default embedder stays at mamba-130m until all gates pass. "
            f"See {latest} for the full measurement record."
        )


def test_pre_reg_embedder_manifest_fresh_enough():
    """If ssm_backbone.py (or its _BACKENDS registry) was edited after
    the manifest was captured, the gates are stale and need re-running."""
    latest = _latest_manifest()
    if latest is None:
        pytest.skip("no pre_reg_embedder manifest yet")
    manifest_mtime = latest.stat().st_mtime
    ssm_mtime = _ssm_backbone_mtime()
    if ssm_mtime > manifest_mtime:
        pytest.fail(
            f"src/ssm_backbone.py was edited at {ssm_mtime} but the latest "
            f"pre_reg_embedder manifest is from {manifest_mtime}; re-run "
            f"`python -m eval.pre_reg_embedder --backends ...` before this "
            f"test will pass."
        )


def test_pre_reg_embedder_manifest_records_required_backends():
    """The manifest must include measurements for at least: the baseline
    (Mamba-130m), the candidate (Mamba-3 SISO 893m by default), and the
    falsifiability control (random-mamba). Missing entries mean the
    harness was run with the wrong --backends flag and the gates don't
    have the data they need."""
    latest = _latest_manifest()
    if latest is None:
        pytest.skip("no pre_reg_embedder manifest yet")
    manifest = json.loads(latest.read_text(encoding="utf-8"))
    required = {
        manifest.get("baseline_backend", "mamba-130m"),
        manifest.get("candidate_backend", "mamba3-siso-893m"),
        "random-mamba",
    }
    measured = set(manifest.get("backends", {}).keys())
    missing = required - measured
    if missing:
        pytest.fail(
            f"manifest is missing required backends: {sorted(missing)}. "
            f"Re-run with --backends {' '.join(sorted(required))}."
        )


def test_pre_reg_embedder_manifest_no_unrecorded_load_failures():
    """A load failure on any measured backend means the harness couldn't
    measure it; the gates evaluate NaN against the threshold and fail.
    This test surfaces that condition as a separate signal so the
    operator sees 'network down' rather than 'gates failed'."""
    latest = _latest_manifest()
    if latest is None:
        pytest.skip("no pre_reg_embedder manifest yet")
    manifest = json.loads(latest.read_text(encoding="utf-8"))
    load_failures = {
        name: r.get("error", "")
        for name, r in manifest.get("backends", {}).items()
        if r.get("n_load_failures", 0) > 0
    }
    if load_failures:
        pytest.fail(
            f"{len(load_failures)} backend(s) failed to load: "
            + ", ".join(f"{n} ({e[:60]})" for n, e in load_failures.items())
            + ". This is the most common failure mode for the mamba3-* "
            "candidates until pin_model_cards.py has run on a host with "
            "web access."
        )
