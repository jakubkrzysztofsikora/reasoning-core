"""Regression tests for rc init's auto-embedder integration.

Covers the wiring added 2026-09-21:
- install_envrc() in src/_init.py calls embedder_tier.decide() and
  appends the auto-pick to .envrc.
- The chosen backend and tier are recorded in the install manifest.
- download_default_model() uses the picked backend's checkpoint +
  revision, not the hardcoded MAMBA_130M_REPO constants.
- Operator-pinned RC_EMBEDDER is honoured.
- Oversized backend warning is surfaced.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from src import _init as _init_mod
from src import embedder_tier


@pytest.fixture(autouse=True)
def _clean_env():
    """Strip RC_EMBEDDER + RC_ALLOW_OVERSIZED_BACKBONE for test isolation."""
    for var in ("RC_EMBEDDER", "RC_ALLOW_OVERSIZED_BACKBONE"):
        os.environ.pop(var, None)
    yield
    for var in ("RC_EMBEDDER", "RC_ALLOW_OVERSIZED_BACKBONE"):
        os.environ.pop(var, None)


# ---------------------------------------------------------------------------
# install_envrc writes the auto-picked backend into .envrc
# ---------------------------------------------------------------------------

def test_install_envrc_writes_autopicked_backend(tmp_path):
    target = tmp_path
    manifest = target / ".reasoning-core" / "install.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.touch()
    result = _init_mod.InitResult(target=target)

    # Force the auto-pick to a known backend (mamba3-siso-893m for 12 GiB host).
    fake_decision = embedder_tier.TierDecision(
        backend="mamba3-siso-893m",
        tier="large",
        estimated_working_set_gb=4.475,
        available_ram_gb=12.0,
        fit_margin_gb=7.525,
        reason="forced by test",
    )
    with mock.patch.object(embedder_tier, "decide", return_value=fake_decision):
        _init_mod.install_envrc(
            target,
            manifest,
            substitutions={"RC_PYTHON": "/usr/bin/python3", "RC_REPO": str(target)},
            result=result,
        )

    envrc_text = (target / ".envrc").read_text()
    assert "export RC_EMBEDDER=mamba3-siso-893m" in envrc_text
    # Manifest should record the tier + backend + working_set for later verification.
    manifest_lines = manifest.read_text().splitlines()
    assert any("embedder_tier=large" in line for line in manifest_lines)
    assert any("embedder_backend=mamba3-siso-893m" in line for line in manifest_lines)
    assert any("embedder_working_set_gb=4.475" in line for line in manifest_lines)


def test_install_envrc_honours_operator_pin(tmp_path, monkeypatch):
    """If the operator sets RC_EMBEDDER, the auto-pick path is skipped
    and the pinned backend is used."""
    monkeypatch.setenv("RC_EMBEDDER", "mamba-130m")
    target = tmp_path
    manifest = target / ".reasoning-core" / "install.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.touch()
    result = _init_mod.InitResult(target=target)

    fake_decision = embedder_tier.TierDecision(
        backend="mamba-130m",
        tier="operator-pinned",
        estimated_working_set_gb=0.625,
        available_ram_gb=8.0,
        fit_margin_gb=7.375,
        reason="operator pinned mamba-130m via RC_EMBEDDER",
    )
    with mock.patch.object(embedder_tier, "decide", return_value=fake_decision) as m:
        _init_mod.install_envrc(
            target,
            manifest,
            substitutions={"RC_PYTHON": "/usr/bin/python3", "RC_REPO": str(target)},
            result=result,
        )
        # decide() must have been called with the operator-pinned backend.
        _, kwargs = m.call_args
        assert kwargs["requested_backend"] == "mamba-130m"

    envrc_text = (target / ".envrc").read_text()
    assert "export RC_EMBEDDER=mamba-130m" in envrc_text
    assert result.warned == []


def test_install_envrc_warns_on_oversized_pin(tmp_path, monkeypatch):
    """If the operator pins a backend that doesn't fit, install_envrc
    surfaces a warning but still writes the .envrc (rc_cli refuses to
    start unless RC_ALLOW_OVERSIZED_BACKBONE=1)."""
    monkeypatch.setenv("RC_EMBEDDER", "mamba3-siso-1.5b")
    target = tmp_path
    manifest = target / ".reasoning-core" / "install.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.touch()
    result = _init_mod.InitResult(target=target)

    fake_decision = embedder_tier.TierDecision(
        backend="mamba3-siso-1.5b",
        tier="operator-pinned",
        estimated_working_set_gb=7.5,
        available_ram_gb=4.0,
        fit_margin_gb=-3.5,
        reason="forced by test, does not fit",
    )
    with mock.patch.object(embedder_tier, "decide", return_value=fake_decision):
        _init_mod.install_envrc(
            target,
            manifest,
            substitutions={"RC_PYTHON": "/usr/bin/python3", "RC_REPO": str(target)},
            result=result,
        )
    assert any("does not fit" in w for w in result.warned)
    envrc_text = (target / ".envrc").read_text()
    assert "export RC_EMBEDDER=mamba3-siso-1.5b" in envrc_text


def test_install_envrc_warns_when_pick_is_oversized(tmp_path):
    """If the auto-pick leaves <2 GB of headroom, surface a warning."""
    target = tmp_path
    manifest = target / ".reasoning-core" / "install.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.touch()
    result = _init_mod.InitResult(target=target)

    fake_decision = embedder_tier.TierDecision(
        backend="mamba3-siso-1.5b",
        tier="large",
        estimated_working_set_gb=7.5,
        available_ram_gb=10.0,
        fit_margin_gb=1.5,  # <2.0 -> oversized
        reason="forced by test, oversized",
    )
    with mock.patch.object(embedder_tier, "decide", return_value=fake_decision):
        _init_mod.install_envrc(
            target,
            manifest,
            substitutions={"RC_PYTHON": "/usr/bin/python3", "RC_REPO": str(target)},
            result=result,
        )
    assert any("headroom" in w for w in result.warned)


def test_install_envrc_swallows_sizer_exceptions(tmp_path):
    """If the sizer raises (e.g. detection fails), install_envrc must
    still complete; the failure is recorded as a warning."""
    target = tmp_path
    manifest = target / ".reasoning-core" / "install.manifest"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.touch()
    result = _init_mod.InitResult(target=target)

    with mock.patch.object(embedder_tier, "decide", side_effect=RuntimeError("boom")):
        _init_mod.install_envrc(
            target,
            manifest,
            substitutions={"RC_PYTHON": "/usr/bin/python3", "RC_REPO": str(target)},
            result=result,
        )
    assert any("embedder_tier auto-pick failed" in w for w in result.warned)
    assert (target / ".envrc").exists()


# ---------------------------------------------------------------------------
# download_default_model honours the picked backend
# ---------------------------------------------------------------------------

def test_download_default_model_uses_picked_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_EMBEDDER", "mamba3-siso-893m")
    result = _init_mod.InitResult(target=tmp_path)
    calls: list[dict] = []

    def fake_snapshot_download(repo_id, revision, cache_dir):
        calls.append({"repo_id": repo_id, "revision": revision, "cache_dir": cache_dir})

    with mock.patch.dict("sys.modules", {"huggingface_hub": mock.MagicMock(
        snapshot_download=fake_snapshot_download,
    )}):
        ok = _init_mod.download_default_model(result)
    assert ok is True
    assert calls and calls[0]["repo_id"] == "state-spaces/mamba3-siso-893m"
    assert calls[0]["revision"] == "main"
    assert result.model_downloaded is True


def test_download_default_model_falls_back_to_unknown_backend(tmp_path, monkeypatch):
    """If the picked RC_EMBEDDER isn't in _BACKENDS (operator typo),
    download_default_model still tries to fetch it from HF."""
    monkeypatch.setenv("RC_EMBEDDER", "huggingface/some-experimental-encoder")
    result = _init_mod.InitResult(target=tmp_path)
    calls: list[dict] = []

    def fake_snapshot_download(repo_id, revision, cache_dir):
        calls.append({"repo_id": repo_id, "revision": revision, "cache_dir": cache_dir})

    with mock.patch.dict("sys.modules", {"huggingface_hub": mock.MagicMock(
        snapshot_download=fake_snapshot_download,
    )}):
        ok = _init_mod.download_default_model(result)
    assert ok is True
    assert calls[0]["repo_id"] == "huggingface/some-experimental-encoder"
    assert calls[0]["revision"] == "main"
