"""Tests for eval/pin_model_cards.py -- the Mamba-3 pre-flight recorder.

The full HuggingFace round-trip requires network + the Mamba-3 model
cards to be public. These tests cover the offline code paths
(extract_relevant_fields, _check_paper_claim, manifest-writing,
exit codes) and a mocked fetch so the wiring is exercised without
actually hitting HF.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest

from eval import pin_model_cards


# ---------------------------------------------------------------------------
# _extract_relevant_fields
# ---------------------------------------------------------------------------

def test_extract_relevant_fields_keeps_only_documented_keys():
    config = {
        "model_type": "mamba3",
        "hidden_size": 1536,
        "vocab_size": 65536,
        "max_position_embeddings": 16384,
        "d_state": 128,
        "d_conv": 4,
        "expand": 2,
        "n_layer": 24,
        "intermediate_size": 4096,
        "ssm_cfg": {
            "layer": "Mamba3",
            "d_state": 128,
            "expand": 2,
            "chunk_size": 64,
            "is_mimo": False,
        },
        "_name_or_path": "state-spaces/mamba3-siso-893m",  # internal, should be dropped
        "transformers_version": "4.50.0",                  # internal, should be dropped
        "use_cache": True,                                  # internal, should be dropped
    }
    out = pin_model_cards._extract_relevant_fields(config, "mamba3-siso-893m")
    assert out == {
        "model_type": "mamba3",
        "hidden_size": 1536,
        "vocab_size": 65536,
        "max_position_embeddings": 16384,
        "d_state": 128,
        "d_conv": 4,
        "expand": 2,
        "n_layer": 24,
        "intermediate_size": 4096,
        "ssm_layer": "Mamba3",
        "ssm_d_state": 128,
        "ssm_expand": 2,
        "ssm_chunk_size": 64,
        "ssm_is_mimo": False,
        "_backend": "mamba3-siso-893m",
    }


def test_extract_relevant_fields_missing_keys_handled_gracefully():
    """If a model card is missing some keys (which happens for newer
    architectures), the extraction silently drops them rather than
    raising."""
    config = {"model_type": "mamba3", "hidden_size": 1536}
    out = pin_model_cards._extract_relevant_fields(config, "mamba3-siso-893m")
    assert out["model_type"] == "mamba3"
    assert out["hidden_size"] == 1536
    assert "max_position_embeddings" not in out
    assert "vocab_size" not in out
    assert "ssm_layer" not in out  # no ssm_cfg in input


# ---------------------------------------------------------------------------
# _check_paper_claim
# ---------------------------------------------------------------------------

def test_paper_claim_passes_when_card_matches_paper():
    fields = {
        "d_model": 1536,
        "n_layer": 24,
        "vocab_size": 128256,
        "ssm_layer": "Mamba3",
        "ssm_d_state": 128,
        "ssm_is_mimo": False,
    }
    mismatches = pin_model_cards._check_paper_claim("mamba3-siso-893m", fields)
    assert mismatches == []


def test_paper_claim_warns_on_short_context():
    """If the card claims a smaller d_state than the paper, fail."""
    fields = {
        "d_model": 1536,
        "n_layer": 24,
        "vocab_size": 128256,
        "ssm_layer": "Mamba3",
        "ssm_d_state": 64,  # paper says 128 -> mismatch
        "ssm_is_mimo": False,
    }
    mismatches = pin_model_cards._check_paper_claim("mamba3-siso-893m", fields)
    assert any("ssm_cfg.d_state=64" in m for m in mismatches)


def test_paper_claim_warns_on_missing_ssm_cfg():
    fields = {"d_model": 1536, "n_layer": 24, "vocab_size": 128256}
    mismatches = pin_model_cards._check_paper_claim("mamba3-siso-893m", fields)
    # Should report missing ssm_layer and ssm_d_state.
    assert any("ssm_cfg.layer" in m for m in mismatches)
    assert any("ssm_cfg.d_state" in m for m in mismatches)


def test_paper_claim_unknown_backend_returns_warning():
    fields = {"max_position_embeddings": 16384}
    mismatches = pin_model_cards._check_paper_claim("unknown-backend", fields)
    assert any("no paper-claim expectation" in m for m in mismatches)


# ---------------------------------------------------------------------------
# Manifest-writing (with mocked fetch)
# ---------------------------------------------------------------------------

def test_pin_one_records_sha_and_status(monkeypatch, tmp_path):
    """Mock the HF fetch; verify pin_one returns a well-formed entry."""
    sample_config = {
        "model_type": "mamba3",
        "hidden_size": 1536,
        "vocab_size": 65536,
        "max_position_embeddings": 16384,
        "d_state": 128,
        "n_layer": 48,
    }
    monkeypatch.setattr(
        pin_model_cards, "_fetch_config",
        lambda repo, revision, *, hf_token: sample_config,
    )
    entry = pin_model_cards.pin_one("mamba3-siso-893m", "main", hf_token=None)
    assert entry["backend"] == "mamba3-siso-893m"
    assert entry["status"] == "pinned"
    assert entry["fields"]["max_position_embeddings"] == 16384
    assert entry["fields_sha256"] is not None
    assert len(entry["fields_sha256"]) == 64  # sha256 hex
    assert "elapsed_s" in entry


def test_pin_one_records_failure_on_fetch_error(monkeypatch):
    def fail(repo, revision, *, hf_token):
        raise ConnectionError("network unreachable")
    monkeypatch.setattr(pin_model_cards, "_fetch_config", fail)
    entry = pin_model_cards.pin_one("mamba3-siso-893m", "main", hf_token=None)
    assert entry["status"] == "fetch_failed"
    assert "network unreachable" in entry["error"]


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

def _build_pin_fn(sample_config):
    """Build a pin_one mock that mirrors the real code's extraction
    path (sample_config -> extracted fields -> pin entry)."""
    def pin_fn(backend, revision, *, hf_token):
        fields = pin_model_cards._extract_relevant_fields(sample_config, backend)
        return {
            "backend": backend,
            "checkpoint": f"fake/{backend}",
            "revision": revision,
            "status": "pinned",
            "fields": fields,
            "fields_sha256": "deadbeef" * 8,
            "elapsed_s": 0.001,
        }
    return pin_fn


def _run_main(monkeypatch, out_path, pin_fn):
    """Invoke main() in-process with a mocked pin_one. Returns the
    return code. subprocess-based invocation is not used because
    monkeypatch attributes don't propagate to fresh Python processes.
    """
    monkeypatch.setattr(pin_model_cards, "pin_one", pin_fn)
    monkeypatch.setattr(
        sys, "argv",
        ["pin_model_cards", "--backends", "mamba3-siso-893m", "--out", str(out_path)],
    )
    return pin_model_cards.main()


def test_main_exits_0_on_all_pins_success(monkeypatch, tmp_path):
    sample_config = {
        "model_type": "mamba3",
        "d_model": 1536,
        "n_layer": 24,
        "vocab_size": 128256,
        "ssm_cfg": {
            "layer": "Mamba3",
            "d_state": 128,
            "expand": 2,
            "chunk_size": 64,
            "is_mimo": False,
        },
    }
    def fake_pin(backend, revision, *, hf_token):
        return {
            "backend": backend,
            "checkpoint": f"fake/{backend}",
            "revision": revision,
            "status": "pinned",
            "fields": sample_config,
            "fields_sha256": "deadbeef" * 8,
            "elapsed_s": 0.001,
        }
    out_path = tmp_path / "cards.json"
    rc = _run_main(monkeypatch, out_path, _build_pin_fn(sample_config))
    assert rc == 0
    assert out_path.exists()
    manifest = json.loads(out_path.read_text())
    assert manifest["n_pinned"] == 1
    assert manifest["n_fetch_failed"] == 0
    assert manifest["paper_mismatches"] == []


def test_main_exits_2_on_fetch_failure(monkeypatch, tmp_path):
    def fail(backend, revision, *, hf_token):
        return {
            "backend": backend,
            "checkpoint": f"fake/{backend}",
            "revision": revision,
            "status": "fetch_failed",
            "error": "ConnectionError: down",
            "elapsed_s": 0.001,
        }
    out_path = tmp_path / "cards.json"
    rc = _run_main(monkeypatch, out_path, fail)
    assert rc == 2


def test_main_exits_3_on_paper_mismatch(monkeypatch, tmp_path):
    sample_config = {
        "model_type": "mamba3",
        "d_model": 1536,
        "n_layer": 24,
        "vocab_size": 128256,
        "ssm_cfg": {
            "layer": "Mamba3",
            "d_state": 64,  # paper says 128 -> mismatch
            "is_mimo": False,
        },
    }
    out_path = tmp_path / "cards.json"
    rc = _run_main(monkeypatch, out_path, _build_pin_fn(sample_config))
    assert rc == 3
