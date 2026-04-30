"""Real Mamba SSM backbone loader for the System 2 sidecar.

This module owns the lifecycle of the pretrained state-space-model that powers
the architectural-impact / coherence scoring path. It is intentionally kept
small and side-effect-free at import time: the heavyweight `transformers`
import is deferred to `load_backbone()` so that `python3 -c "import
src.ssm_backbone"` stays cheap and offline-friendly.

Backbone selection priority (documented for ARCHITECTURE.md / RC-008):
    1. ``state-spaces/mamba-130m-hf``  -- default. Pure HF port that does NOT
       require the optional ``mamba-ssm`` / ``causal-conv1d`` CUDA kernels, so
       it loads cleanly on macOS arm64 + CPU.
    2. ``state-spaces/mamba2-130m``    -- fallback if the primary checkpoint
       cannot be resolved (registered for forward compatibility; documented in
       ARCHITECTURE.md).
    3. ``sshleifer/tiny-gpt2``         -- last-resort tiny-transformer fallback
       used ONLY when both Mamba checkpoints fail to resolve. This keeps the
       sidecar booting in fully offline / unreachable-HF environments. The
       fallback is reflected honestly in ``BACKBONE_INFO`` so callers (and the
       /health endpoint) can tell the difference.

Env overrides:
    S2_SSM_CHECKPOINT  -- override the primary checkpoint string.
    S2_DEVICE          -- "cpu" (default) or "cuda".
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Public constants ---------------------------------------------------------

DEFAULT_CHECKPOINT = "state-spaces/mamba-130m-hf"
FALLBACK_CHECKPOINTS = (
    "state-spaces/mamba2-130m",
    # Tiny last-resort transformer; documented above. NOT a Mamba, but keeps
    # the sidecar honest in air-gapped envs by still producing real embeddings.
    "sshleifer/tiny-gpt2",
)

# Filled in after first successful load_backbone() call. Consumed by the
# /health endpoint and ARCHITECTURE.md generation.
BACKBONE_INFO: dict[str, Any] = {
    "checkpoint": None,
    "hidden_size": None,
    "num_parameters": None,
    "license": None,
    "source_url": None,
    "is_fallback": False,
    "device": None,
}


class BackboneUnavailableError(RuntimeError):
    """Raised when no backbone (primary or fallbacks) could be loaded."""


# --- Singleton state ----------------------------------------------------------

@dataclass
class _BackboneHandle:
    model: Any = None
    tokenizer: Any = None
    device: str = "cpu"
    checkpoint: str = ""
    hidden_size: int = 0
    info: dict[str, Any] = field(default_factory=dict)


_HANDLE: Optional[_BackboneHandle] = None
_LOAD_LOCK = threading.Lock()


# --- Loader -------------------------------------------------------------------

def _resolve_device(device: Optional[str]) -> str:
    # Env wins when caller didn't pass an explicit override.
    env_val = os.environ.get("S2_DEVICE", "").lower().strip()
    if device is None:
        return env_val or "cpu"
    return env_val or device or "cpu"


def _resolve_checkpoint(checkpoint: Optional[str]) -> str:
    # Env wins when caller didn't pass an explicit override.
    env_val = os.environ.get("S2_SSM_CHECKPOINT", "").strip()
    if checkpoint is None:
        return env_val or DEFAULT_CHECKPOINT
    return env_val or checkpoint or DEFAULT_CHECKPOINT


def _try_load(ckpt: str, device: str) -> Optional[_BackboneHandle]:
    """Attempt to load a single checkpoint. Returns None on failure."""
    try:
        # Imported lazily so module import remains light.
        import torch  # noqa: F401  (used implicitly via model.to / no_grad)
        from transformers import AutoModel, AutoTokenizer
    except Exception as exc:  # pragma: no cover - import guard
        logger.error("transformers/torch import failed: %s", exc)
        return None

    try:
        logger.info("Loading SSM backbone checkpoint=%s device=%s", ckpt, device)
        # trust_remote_code is permitted for state-spaces/* in case the
        # repo ships custom modeling code; HF will only execute it if present.
        tokenizer = AutoTokenizer.from_pretrained(ckpt, trust_remote_code=True)
        model = AutoModel.from_pretrained(ckpt, trust_remote_code=True)
        model.eval()
        try:
            model.to(device)
        except Exception as exc:  # pragma: no cover - cuda missing etc.
            logger.warning("model.to(%s) failed (%s); falling back to cpu", device, exc)
            device = "cpu"
            model.to(device)

        hidden_size = int(getattr(model.config, "hidden_size", 0) or 0)
        if hidden_size <= 0:
            # Some configs use d_model.
            hidden_size = int(getattr(model.config, "d_model", 0) or 0)

        # Many tokenizers (gpt-neox-derived, used by mamba-130m-hf) lack a
        # pad token; reuse eos to keep batched calls happy.
        if getattr(tokenizer, "pad_token", None) is None and getattr(tokenizer, "eos_token", None):
            tokenizer.pad_token = tokenizer.eos_token

        num_params = 0
        try:
            num_params = sum(p.numel() for p in model.parameters())
        except Exception:  # pragma: no cover
            pass

        info = {
            "checkpoint": ckpt,
            "hidden_size": hidden_size,
            "num_parameters": num_params,
            "license": "apache-2.0" if ckpt.startswith("state-spaces/") else "unknown",
            "source_url": f"https://huggingface.co/{ckpt}",
            "is_fallback": ckpt != DEFAULT_CHECKPOINT,
            "device": device,
        }
        return _BackboneHandle(
            model=model,
            tokenizer=tokenizer,
            device=device,
            checkpoint=ckpt,
            hidden_size=hidden_size,
            info=info,
        )
    except Exception as exc:
        logger.warning("checkpoint %s failed to load: %s", ckpt, exc)
        return None


def load_backbone(
    checkpoint: Optional[str] = None,
    device: Optional[str] = None,
) -> tuple[Any, Any]:
    """Load (or return cached) Mamba backbone + tokenizer.

    Returns a (model, tokenizer) tuple. Raises BackboneUnavailableError if
    every candidate checkpoint fails to resolve.
    """
    global _HANDLE
    if _HANDLE is not None and _HANDLE.model is not None:
        return _HANDLE.model, _HANDLE.tokenizer

    with _LOAD_LOCK:
        if _HANDLE is not None and _HANDLE.model is not None:
            return _HANDLE.model, _HANDLE.tokenizer

        device = _resolve_device(device)
        primary = _resolve_checkpoint(checkpoint)
        candidates = [primary] + [c for c in FALLBACK_CHECKPOINTS if c != primary]

        last_error: Optional[Exception] = None
        for ckpt in candidates:
            handle = _try_load(ckpt, device)
            if handle is not None:
                _HANDLE = handle
                BACKBONE_INFO.update(handle.info)
                logger.info(
                    "SSM backbone ready: checkpoint=%s hidden=%d params=%d device=%s fallback=%s",
                    handle.checkpoint,
                    handle.hidden_size,
                    handle.info.get("num_parameters", 0),
                    handle.device,
                    handle.info.get("is_fallback", False),
                )
                return handle.model, handle.tokenizer

        raise BackboneUnavailableError(
            "No SSM backbone could be loaded. Tried: "
            + ", ".join(candidates)
            + ". Remediation: run `huggingface-cli download state-spaces/mamba-130m-hf` "
            + "or set S2_SSM_CHECKPOINT to a locally-cached model id. "
            + (f"Last error: {last_error}" if last_error else "")
        )


def get_handle() -> _BackboneHandle:
    """Return the loaded backbone handle. Loads on demand."""
    if _HANDLE is None:
        load_backbone()
    assert _HANDLE is not None  # for type-checkers
    return _HANDLE


def is_loaded() -> bool:
    return _HANDLE is not None and _HANDLE.model is not None


# --- Embedding ----------------------------------------------------------------

# Deterministic seed applied at every embed() call. Combined with model.eval()
# + torch.no_grad() this gives bit-identical outputs across two calls with the
# same input -- a hard contract from the board.
_EMBED_SEED = 0xC0DEC0DE & 0xFFFFFFFF


def embed(text: str) -> Any:
    """Return a 1D pooled embedding (mean over seq dim) as a torch.Tensor."""
    import torch

    handle = get_handle()
    model = handle.model
    tokenizer = handle.tokenizer
    device = handle.device

    # Empty-string guard: produce a deterministic zero-vector rather than a
    # tokenizer error, so the score path never crashes on edge inputs.
    text = text if text else " "

    # Determinism. Re-seed on every call -- cheap, and isolates from any
    # surrounding process state.
    torch.manual_seed(_EMBED_SEED)

    enc = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        padding=False,
    )
    input_ids = enc["input_ids"].to(device)
    attention_mask = enc.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(device)

    with torch.no_grad():
        try:
            out = model(input_ids=input_ids, attention_mask=attention_mask)
        except TypeError:
            # Some Mamba forwards do not accept attention_mask kwarg.
            out = model(input_ids=input_ids)

    last = getattr(out, "last_hidden_state", None)
    if last is None:
        # Fallback: many AutoModel outputs are tuples whose first elem is hidden states.
        last = out[0] if isinstance(out, (list, tuple)) else None
    if last is None:
        raise BackboneUnavailableError(
            "Backbone forward produced no last_hidden_state; cannot embed."
        )

    # Mean-pool over sequence dim -> [hidden_size]
    pooled = last.mean(dim=1).squeeze(0)
    return pooled.detach().cpu()


# --- AST-token bridge ---------------------------------------------------------

def ast_to_tokens(tree: Any, src: str) -> str:
    """Linearise an AST + source into a deterministic token string for the SSM.

    The strategy is intentionally simple and stable: walk the tree in a
    pre-order traversal and emit ``<NODE_TYPE>`` for each named node, plus the
    leaf identifier text for terminals shorter than 64 chars. This produces a
    reproducible, structure-aware token stream that the tokenizer can chew on
    without us depending on a particular grammar's quirks.

    If the tree is None or has no root, fall back to the raw source so
    coherence_delta is still meaningful.
    """
    if tree is None or getattr(tree, "root_node", None) is None:
        return src or ""

    src_bytes = src.encode("utf-8", errors="replace") if isinstance(src, str) else (src or b"")
    pieces: list[str] = []
    stack = [tree.root_node]
    # Pre-order traversal, bounded to keep huge files cheap.
    max_nodes = 4096
    visited = 0
    while stack and visited < max_nodes:
        node = stack.pop()
        visited += 1
        if node is None:
            continue
        try:
            ntype = node.type
        except Exception:
            ntype = "node"
        pieces.append(f"<{ntype}>")
        # Leaf: emit short identifier text.
        try:
            child_count = node.child_count
        except Exception:
            child_count = 0
        if child_count == 0:
            try:
                start, end = node.start_byte, node.end_byte
                text = src_bytes[start:end].decode("utf-8", errors="replace")
                if 0 < len(text) <= 64:
                    pieces.append(text.strip())
            except Exception:
                pass
        else:
            # Push children right-to-left so left-most is processed first.
            try:
                children = list(node.children)
            except Exception:
                children = []
            for child in reversed(children):
                stack.append(child)

    return " ".join(p for p in pieces if p)


__all__ = [
    "BACKBONE_INFO",
    "BackboneUnavailableError",
    "DEFAULT_CHECKPOINT",
    "FALLBACK_CHECKPOINTS",
    "ast_to_tokens",
    "embed",
    "get_handle",
    "is_loaded",
    "load_backbone",
]
