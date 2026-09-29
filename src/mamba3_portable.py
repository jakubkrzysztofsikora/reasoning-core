"""Torch CPU/MPS inference for the pinned Mamba-3 SISO checkpoint.

Architecture and recurrence follow state-spaces/mamba's Mamba3, Block,
GatedMLP and tests/ops/triton/test_mamba3_siso.py reference implementation.
This intentionally supports only the public SISO architecture without attention.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F


CHECKPOINT = "state-spaces/mamba3-siso-893m"
CHECKPOINTS = {
    CHECKPOINT,
    "state-spaces/mamba3-siso-1.5b",
}
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _rms(x: torch.Tensor, weight: torch.Tensor, eps: float = 1e-5) -> torch.Tensor:
    result = x.float() * torch.rsqrt(x.float().square().mean(dim=-1, keepdim=True) + eps)
    return (result * weight.float()).to(x.dtype)


def _rotate(x: torch.Tensor, angle: torch.Tensor) -> torch.Tensor:
    pairs = x.float().reshape(*x.shape[:-1], -1, 2)
    count = angle.shape[-1]
    left, right = pairs.unbind(dim=-1)
    cosine, sine = angle.cos(), angle.sin()
    rotated_left = torch.cat((left[..., :count] * cosine - right[..., :count] * sine, left[..., count:]), -1)
    rotated_right = torch.cat((left[..., :count] * sine + right[..., :count] * cosine, right[..., count:]), -1)
    return torch.stack((rotated_left, rotated_right), -1).flatten(-2)


class _SisoMixer(nn.Module):
    def __init__(self, d_model: int, cfg: dict):
        super().__init__()
        self.d_state = int(cfg["d_state"])
        self.headdim = int(cfg["headdim"])
        self.d_inner = int(d_model * cfg["expand"])
        if self.d_inner % self.headdim or self.d_state % 2:
            raise ValueError("unsupported Mamba3 SISO dimensions")
        self.nheads = self.d_inner // self.headdim
        self.ngroups = int(cfg["ngroups"])
        if self.nheads % self.ngroups:
            raise ValueError("Mamba3 head/group mismatch")
        self.num_angles = int(self.d_state * cfg["rope_fraction"]) // 2
        self.a_floor = float(cfg["A_floor"])
        projected = 2 * self.d_inner + 2 * self.d_state * self.ngroups + 3 * self.nheads + self.num_angles
        self.in_proj = nn.Linear(d_model, projected, bias=False)
        self.dt_bias = nn.Parameter(torch.empty(self.nheads))
        self.B_bias = nn.Parameter(torch.empty(self.nheads, 1, self.d_state))
        self.C_bias = nn.Parameter(torch.empty(self.nheads, 1, self.d_state))
        self.B_norm = nn.Module()
        self.B_norm.weight = nn.Parameter(torch.empty(self.d_state))
        self.C_norm = nn.Module()
        self.C_norm.weight = nn.Parameter(torch.empty(self.d_state))
        self.D = nn.Parameter(torch.empty(self.nheads))
        self.out_proj = nn.Linear(self.d_inner, d_model, bias=False)

    def forward(self, u: torch.Tensor) -> torch.Tensor:
        batch, length, _ = u.shape
        h, p, n, g = self.nheads, self.headdim, self.d_state, self.ngroups
        z, x, b, c, dd_dt, dd_a, trap, angles = torch.split(
            self.in_proj(u),
            [self.d_inner, self.d_inner, g * n, g * n, h, h, h, self.num_angles], -1,
        )
        x, z = x.reshape(batch, length, h, p), z.reshape(batch, length, h, p)
        b = _rms(b.reshape(batch, length, g, n), self.B_norm.weight)
        c = _rms(c.reshape(batch, length, g, n), self.C_norm.weight)
        b = b.repeat_interleave(h // g, dim=2).float() + self.B_bias[:, 0].float()
        c = c.repeat_interleave(h // g, dim=2).float() + self.C_bias[:, 0].float()
        dt = F.softplus(dd_dt.float() + self.dt_bias.float())
        a = -(dd_a.float().clamp_min(0) + (1 - dd_a.float().clamp_max(0)).reciprocal())
        adt = a.clamp(max=-self.a_floor) * dt
        angles = torch.tanh(angles.float()) * math.pi
        angle_deltas = angles.unsqueeze(2) * dt.unsqueeze(-1)
        angle_states = torch.remainder(torch.cumsum(angle_deltas, dim=1), 2 * math.pi)

        q_all = _rotate(c, angle_states)
        k_all = _rotate(b, angle_states)
        v_all = x.float()

        alpha_all = adt.exp()
        trap_sig = trap.float().sigmoid()
        gamma_all = trap_sig * dt
        beta_all = (1.0 - trap_sig) * dt * alpha_all
        silu_z = F.silu(z.float())

        state = torch.zeros(batch, h, p, n, device=u.device)
        old_k = torch.zeros(batch, h, n, device=u.device)
        old_v = torch.zeros(batch, h, p, device=u.device)
        outputs = []
        d_float = self.D.float()[None, :, None]

        for t in range(length):
            q = q_all[:, t]
            k = k_all[:, t]
            v = v_all[:, t]
            alpha = alpha_all[:, t]
            gamma = gamma_all[:, t]
            beta = beta_all[:, t]

            state = (state * alpha[..., None, None]
                     + beta[..., None, None] * old_v[..., :, None] * old_k[..., None, :]
                     + gamma[..., None, None] * v[..., :, None] * k[..., None, :])
            y = (state * q[..., None, :]).sum(-1) + d_float * v
            outputs.append((y * silu_z[:, t]).to(u.dtype))
            old_k, old_v = k, v
        return self.out_proj(torch.stack(outputs, dim=1).reshape(batch, length, self.d_inner))


class _Block(nn.Module):
    def __init__(self, d_model: int, d_intermediate: int, ssm_cfg: dict):
        super().__init__()
        self.norm = nn.Module()
        self.norm.weight = nn.Parameter(torch.empty(d_model))
        self.mixer = _SisoMixer(d_model, ssm_cfg)
        self.norm2 = nn.Module()
        self.norm2.weight = nn.Parameter(torch.empty(d_model))
        self.mlp = nn.Module()
        self.mlp.fc1 = nn.Linear(d_model, 2 * d_intermediate, bias=False)
        self.mlp.fc2 = nn.Linear(d_intermediate, d_model, bias=False)

    def forward(self, hidden: torch.Tensor, residual: torch.Tensor | None):
        residual = hidden.float() if residual is None else hidden.float() + residual
        hidden = self.mixer(_rms(residual.to(hidden.dtype), self.norm.weight))
        residual = residual + hidden.float()
        projected = self.mlp.fc1(_rms(residual.to(hidden.dtype), self.norm2.weight))
        value, gate = projected.chunk(2, dim=-1)
        return self.mlp.fc2(value * F.silu(gate)), residual


@dataclass
class _Output:
    last_hidden_state: torch.Tensor


class Mamba3SisoPortable(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        _validate_config(cfg)
        self.config = cfg
        self.backbone = nn.Module()
        self.backbone.embedding = nn.Embedding(cfg["vocab_size"], cfg["d_model"])
        self.backbone.layers = nn.ModuleList(
            _Block(cfg["d_model"], cfg["d_intermediate"], cfg["ssm_cfg"])
            for _ in range(cfg["n_layer"])
        )
        self.backbone.norm_f = nn.Module()
        self.backbone.norm_f.weight = nn.Parameter(torch.empty(cfg["d_model"]))
        self.lm_head = nn.Linear(cfg["d_model"], cfg["vocab_size"], bias=False)
        self.lm_head.weight = self.backbone.embedding.weight

    def forward(self, input_ids: torch.Tensor, attention_mask=None, **kwargs) -> _Output:
        if kwargs:
            raise ValueError(f"unsupported Mamba3 arguments: {sorted(kwargs)}")
        if input_ids.ndim != 2 or input_ids.shape[1] < 1:
            raise ValueError("input_ids must have shape (batch, positive sequence length)")
        if attention_mask is not None and not bool(torch.all(attention_mask == 1)):
            raise ValueError("Mamba3 SISO does not support padded attention masks")
        hidden = self.backbone.embedding(input_ids)
        residual = None
        for layer in self.backbone.layers:
            hidden, residual = layer(hidden, residual)
        hidden = _rms((hidden.float() + residual).to(hidden.dtype), self.backbone.norm_f.weight)
        return _Output(last_hidden_state=hidden)


def _validate_config(cfg: dict) -> None:
    required = {"d_model", "d_intermediate", "n_layer", "vocab_size", "ssm_cfg", "rms_norm", "residual_in_fp32", "tie_embeddings"}
    if not required <= cfg.keys() or not all(isinstance(cfg[k], int) and cfg[k] > 0 for k in ("d_model", "d_intermediate", "n_layer", "vocab_size")):
        raise ValueError("incomplete Mamba3 SISO config")
    ssm = cfg["ssm_cfg"]
    expected = {"layer", "d_state", "expand", "headdim", "ngroups", "rope_fraction", "A_floor", "is_mimo", "is_outproj_norm"}
    if (not expected <= ssm.keys() or ssm["layer"] != "Mamba3" or ssm["is_mimo"]
            or ssm["is_outproj_norm"] or cfg.get("attn_layer_idx")
            or not cfg["rms_norm"] or not cfg["residual_in_fp32"] or not cfg["tie_embeddings"]
            or ssm["rope_fraction"] not in (0.5, 1.0)):
        raise ValueError("unsupported Mamba3 architecture")


def load_mamba3_siso(checkpoint: str, revision: str, device: str = "cpu", dtype=None) -> Mamba3SisoPortable:
    """Load only a SHA-pinned SISO checkpoint; reject missing/unexpected keys."""
    if checkpoint not in CHECKPOINTS or not _SHA.fullmatch(revision):
        raise ValueError("Mamba3 SISO requires its allowlisted repo and pinned commit SHA")
    if device not in ("cpu", "mps"):
        raise ValueError("portable Mamba3 SISO supports cpu and mps only")
    from huggingface_hub import hf_hub_download

    config_path = hf_hub_download(checkpoint, "config.json", revision=revision)
    weight_path = hf_hub_download(checkpoint, "pytorch_model.bin", revision=revision)
    cfg = json.loads(Path(config_path).read_text())
    with torch.device("meta"):
        model = Mamba3SisoPortable(cfg)
    weights = torch.load(weight_path, map_location="cpu", weights_only=True, mmap=True)
    model.load_state_dict(weights, strict=True, assign=True)
    if model.lm_head.weight.shape != model.backbone.embedding.weight.shape or not torch.equal(
        model.lm_head.weight, model.backbone.embedding.weight
    ):
        raise ValueError("checkpoint untied embedding weights conflict with config")
    model.lm_head.weight = model.backbone.embedding.weight
    if dtype is not None:
        model = model.to(dtype=dtype)
    return model.to(device).eval()
