"""Embed a function's source into an L2-normalised vector for the near-duplicate
oracle's Stage-1 cosine shortlist.

Thin wrapper over :func:`ssm_backbone.embed` so the oracle and the fixture
generator share one embedder: ``unixcoder-base`` (a code-pretrained encoder),
defaulted below so a live advisory never uses the general-purpose backbone.
Torch-dependent -- deliberately NOT imported by the pure ``dup_index`` /
``dup_oracle`` modules or their tests, so the offline gate never loads a model.
"""
from __future__ import annotations

import os

import numpy as np

from .ssm_backbone import embed

# Default to the code-pretrained encoder for the shortlist. Without this a live
# advisory would embed with the general-purpose default (mamba-130m) -- the wrong
# model for code near-duplicates. An explicit RC_EMBEDDER is still respected.
os.environ.setdefault("RC_EMBEDDER", "unixcoder-base")


def _to_numpy(vec) -> np.ndarray:
    """Coerce a torch tensor / tensor-like pooled embedding to a 1-D float32
    array."""
    if hasattr(vec, "detach"):  # torch.Tensor
        vec = vec.detach().cpu().numpy()
    return np.asarray(vec, dtype=np.float32).reshape(-1)


def embed_function(source: str) -> np.ndarray:
    """Return the L2-normalised embedding of ``source`` (so cosine == dot).

    A zero vector (degenerate input) is returned unnormalised rather than
    dividing by zero.
    """
    arr = _to_numpy(embed(source))
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0.0 else arr
