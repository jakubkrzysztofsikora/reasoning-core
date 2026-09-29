"""Small CPU checks for the Mamba-3 SISO portable inference path."""

import pytest

torch = pytest.importorskip("torch")

from src.mamba3_portable import Mamba3SisoPortable, load_mamba3_siso  # noqa: E402


def _config():
    return {
        "d_model": 8, "d_intermediate": 16, "n_layer": 2, "vocab_size": 32,
        "rms_norm": True, "residual_in_fp32": True, "fused_add_norm": True,
        "tie_embeddings": True, "attn_layer_idx": [],
        "ssm_cfg": {
            "layer": "Mamba3", "d_state": 8, "expand": 2, "headdim": 4,
            "ngroups": 1, "rope_fraction": 0.5, "A_floor": 1e-4,
            "is_mimo": False, "is_outproj_norm": False,
        },
    }


def test_tiny_sequence_is_finite_and_causal():
    torch.manual_seed(7)
    model = Mamba3SisoPortable(_config()).eval()
    # The production loader replaces empty parameters with checkpoint weights.
    for parameter in model.parameters():
        torch.nn.init.uniform_(parameter, -0.1, 0.1)
    with torch.inference_mode():
        full = model(torch.tensor([[1, 2, 3]])).last_hidden_state
        prefix = model(torch.tensor([[1, 2]])).last_hidden_state
    assert full.shape == (1, 3, 8)
    assert torch.isfinite(full).all()
    torch.testing.assert_close(full[:, :2], prefix, atol=1e-5, rtol=1e-5)


def test_strict_config_and_mask():
    cfg = _config()
    cfg["ssm_cfg"]["is_mimo"] = True
    with pytest.raises(ValueError, match="unsupported"):
        Mamba3SisoPortable(cfg)
    model = Mamba3SisoPortable(_config())
    with pytest.raises(ValueError, match="padded"):
        model(torch.tensor([[1, 2]]), attention_mask=torch.tensor([[1, 0]]))


def test_loader_rejects_mutable_revision_before_download():
    with pytest.raises(ValueError, match="pinned"):
        load_mamba3_siso("state-spaces/mamba3-siso-893m", "main")
    with pytest.raises(ValueError, match="allowlisted"):
        load_mamba3_siso("other/repo", "e205b6e6d6075d089140d2e9170970aabc05c481")
