"""Multi-backend embedder loader for the System 2 sidecar.

This module owns the lifecycle of the embedder that powers the
architectural-impact / coherence scoring path. It is intentionally kept
small and side-effect-free at import time: the heavyweight `transformers`
import is deferred to `load_backbone()` so that `python3 -c "import
src.ssm_backbone"` stays cheap and offline-friendly.

Backends (selected via ``RC_EMBEDDER`` env):
    codestral-mamba  -- default. mistralai/Mamba-Codestral-7B-v0.1 (Apache 2.0,
                       code-pretrained Mamba-2, 256K context cap -> 8192).
    mamba-130m       -- legacy fallback. state-spaces/mamba-130m-hf (Pile-LM).
    bge-code         -- BAAI/bge-code-v1 (code-specialised transformer, ~4GB).
    unixcoder-base   -- microsoft/unixcoder-base (code transformer baseline).
    random-mamba     -- randomly-initialised Mamba-2 control for falsifiability.

Legacy env (backward compat):
    S2_SSM_CHECKPOINT  -- override for mamba-130m path ONLY.
    S2_DEVICE          -- "cpu" (default) or "cuda".
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Backend registry --------------------------------------------------------


@dataclass(frozen=True)
class _EmbedderBackend:
    """Static configuration for one embedder backend."""

    name: str                      # RC_EMBEDDER value
    checkpoint: str                # HuggingFace repo id
    pooling: str                   # "mean" or "cls"
    max_seq_len: int
    hidden_size: int               # expected / known hidden dimension
    revision: str                  # "main" or pinned SHA
    license: str
    is_code_pretrained: bool = True


MAX_SEQ_LEN_CODESTRAL: int = 8192

_BACKENDS: dict[str, _EmbedderBackend] = {
    "codestral-mamba": _EmbedderBackend(
        name="codestral-mamba",
        checkpoint="mistralai/Mamba-Codestral-7B-v0.1",
        pooling="mean",
        max_seq_len=MAX_SEQ_LEN_CODESTRAL,
        hidden_size=4096,
        revision="main",
        license="apache-2.0",
    ),
    "mamba-130m": _EmbedderBackend(
        name="mamba-130m",
        checkpoint="state-spaces/mamba-130m-hf",
        pooling="mean",
        max_seq_len=512,
        hidden_size=768,
        revision="1e76775f628fbf1350fbe4dbb3d971ba64af25a1",
        license="apache-2.0",
    ),
    "bge-code": _EmbedderBackend(
        name="bge-code",
        checkpoint="BAAI/bge-code-v1",
        pooling="cls",
        max_seq_len=MAX_SEQ_LEN_CODESTRAL,
        hidden_size=768,
        revision="main",
        license="apache-2.0",
    ),
    "unixcoder-base": _EmbedderBackend(
        name="unixcoder-base",
        checkpoint="microsoft/unixcoder-base",
        pooling="cls",
        max_seq_len=512,
        hidden_size=768,
        revision="main",
        license="mit",
    ),
    "random-mamba": _EmbedderBackend(
        name="random-mamba",
        checkpoint="__random_mamba__",
        pooling="mean",
        max_seq_len=512,
        hidden_size=768,
        revision="",
        license="n/a",
        is_code_pretrained=False,
    ),
}

_DEFAULT_BACKEND_NAME: str = "codestral-mamba"

# Legacy constants — kept for API compat; new code uses _EmbedderBackend.
DEFAULT_CHECKPOINT = _BACKENDS["mamba-130m"].checkpoint
FALLBACK_CHECKPOINTS = (
    "state-spaces/mamba2-130m",
    "sshleifer/tiny-gpt2",
)
_ALLOWED_CHECKPOINTS = frozenset(
    {DEFAULT_CHECKPOINT, *FALLBACK_CHECKPOINTS}
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PINNED_REVISIONS: dict[str, str] = {
    "state-spaces/mamba-130m-hf": "1e76775f628fbf1350fbe4dbb3d971ba64af25a1",
    "state-spaces/mamba2-130m":   "3a5aea0c25d0fb43cc360e2c2aac82c26e3eed49",
    "sshleifer/tiny-gpt2":        "5f91d94bd9cd7190a9f3216ff93cd1dd95f2c7be",
}


# Filled in after first successful load_backbone() call.
BACKBONE_INFO: dict[str, Any] = {
    "checkpoint": None,
    "hidden_size": None,
    "num_parameters": None,
    "license": None,
    "source_url": None,
    "is_fallback": False,
    "device": None,
    "embedder_role": "feature_extractor",
    "embedder_backend": None,
}


class BackboneUnavailableError(RuntimeError):
    """Raised when no backbone could be loaded."""


# --- Singleton state ----------------------------------------------------------

@dataclass
class _BackboneHandle:
    model: Any = None
    tokenizer: Any = None
    device: str = "cpu"
    checkpoint: str = ""
    hidden_size: int = 0
    info: dict[str, Any] = field(default_factory=dict)
    backend: Optional[_EmbedderBackend] = None


_HANDLE: Optional[_BackboneHandle] = None
_LOAD_LOCK = threading.Lock()


# --- Helpers ------------------------------------------------------------------


def _resolve_device(device: Optional[str]) -> str:
    env_val = os.environ.get("S2_DEVICE", "").lower().strip()
    if device is None:
        return env_val or "cpu"
    return env_val or device or "cpu"


def _resolve_backend() -> _EmbedderBackend:
    """Select backend from RC_EMBEDDER env (default codestral-mamba)."""
    name = os.environ.get("RC_EMBEDDER", _DEFAULT_BACKEND_NAME).strip().lower()
    if name not in _BACKENDS:
        logger.warning(
            "RC_EMBEDDER=%r not recognised; falling back to %s",
            name, _DEFAULT_BACKEND_NAME,
        )
        name = _DEFAULT_BACKEND_NAME
    return _BACKENDS[name]


def _revision_env_key(repo_id: str) -> str:
    return "RC_" + re.sub(r"[^A-Z0-9]", "_", repo_id.upper()) + "_REVISION"


def _resolve_revision(repo_id: str) -> str:
    """Return a 40-char commit SHA for ``repo_id``. Fails closed on mutable refs.

    Optional override via ``RC_<REPO_SLUG>_REVISION`` env var, which must also
    be a 40-char hex SHA. Branch names like ``main`` are explicitly rejected
    to prevent supply-chain attacks via a malicious upstream push.

    Only applies to checkpoints listed in ``_PINNED_REVISIONS``. Newer
    backbones in the ``_BACKENDS`` registry that carry ``revision="main"``
    bypass this helper via ``_resolve_revision_for_backend`` -- track those
    separately if/when they're added to the allowlist.
    """
    env_key = _revision_env_key(repo_id)
    override = os.environ.get(env_key, "").strip()
    if override:
        if not _SHA_RE.match(override):
            raise BackboneUnavailableError(
                f"{env_key}={override!r} is not a 40-char hex commit SHA. "
                f"Mutable refs (branches/tags) are forbidden."
            )
        return override
    pin = _PINNED_REVISIONS.get(repo_id)
    if not pin:
        raise BackboneUnavailableError(
            f"No pinned revision for {repo_id!r}. Add it to _PINNED_REVISIONS."
        )
    return pin


def _resolve_checkpoint(checkpoint: Optional[str]) -> str:
    """Return an allowlisted checkpoint id for the legacy checkpoint-based path.

    Env wins when caller didn't pass an explicit override. Raises
    ``BackboneUnavailableError`` if the resolved id isn't in
    ``_ALLOWED_CHECKPOINTS`` -- this protects the legacy load path from
    arbitrary HF repos.
    """
    env_val = os.environ.get("S2_SSM_CHECKPOINT", "").strip()
    if checkpoint is None:
        resolved = env_val or DEFAULT_CHECKPOINT
    else:
        resolved = env_val or checkpoint or DEFAULT_CHECKPOINT
    if resolved not in _ALLOWED_CHECKPOINTS:
        raise BackboneUnavailableError(
            f"checkpoint {resolved!r} is not in the allowlist "
            f"{sorted(_ALLOWED_CHECKPOINTS)}; refusing to load with "
            f"trust_remote_code=True against an arbitrary HF repo"
        )
    return resolved


def _try_load(ckpt: str, device: str) -> Optional["_BackboneHandle"]:
    """Legacy single-checkpoint loader. Kept for the security test surface
    and for callers that bypass the ``RC_EMBEDDER`` registry. Returns ``None``
    on failure rather than raising.
    """
    try:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer
    except Exception as exc:  # pragma: no cover
        logger.error("transformers/torch import failed: %s", exc)
        return None
    try:
        revision = _resolve_revision(ckpt)
        logger.info(
            "Loading SSM backbone checkpoint=%s revision=%s device=%s",
            ckpt, revision[:12], device,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            ckpt, revision=revision, trust_remote_code=False,
        )
        model = AutoModel.from_pretrained(
            ckpt, revision=revision, trust_remote_code=False,
        )
        model.eval()
        model.to(device)
        hidden_size = int(getattr(model.config, "hidden_size", 0)) or int(
            getattr(model.config, "d_model", 0)
        )
        return _BackboneHandle(
            model=model,
            tokenizer=tokenizer,
            device=device,
            checkpoint=ckpt,
            hidden_size=hidden_size,
            info={"checkpoint": ckpt, "revision": revision},
        )
    except Exception as exc:
        logger.warning("checkpoint %s failed to load: %s", ckpt, exc)
        return None


def _resolve_revision_for_backend(backend: _EmbedderBackend) -> str | None:
    """Return revision string or None when not required (e.g. random-mamba)."""
    if backend.name == "random-mamba":
        return None
    # Legacy SHA pins for mamba-130m / tiny-gpt2
    pin = _PINNED_REVISIONS.get(backend.checkpoint)
    if pin:
        return pin
    # For models using "main" we trust the HF Hub cache
    if backend.revision == "main":
        return "main"
    return backend.revision or None


# --- Loader -------------------------------------------------------------------


def _try_load_backend(backend: _EmbedderBackend, device: str) -> Optional[_BackboneHandle]:
    """Attempt to load a single backend. Returns None on failure."""
    try:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer, AutoConfig
    except Exception as exc:  # pragma: no cover
        logger.error("transformers/torch import failed: %s", exc)
        return None

    try:
        if backend.name == "random-mamba":
            return _try_load_random_mamba(backend, device)

        revision = _resolve_revision_for_backend(backend)
        logger.info(
            "Loading embedder backend=%s checkpoint=%s revision=%s device=%s",
            backend.name, backend.checkpoint,
            (revision or "latest")[:12], device,
        )

        load_kwargs: dict[str, Any] = {"trust_remote_code": False}
        if revision:
            load_kwargs["revision"] = revision

        tokenizer = AutoTokenizer.from_pretrained(
            backend.checkpoint, **load_kwargs,
        )
        model = AutoModel.from_pretrained(
            backend.checkpoint, **load_kwargs,
        )
        model.eval()
        try:
            model.to(device)
        except Exception as exc:  # pragma: no cover
            logger.warning("model.to(%s) failed (%s); falling back to cpu", device, exc)
            device = "cpu"
            model.to(device)

        hidden_size = int(getattr(model.config, "hidden_size", 0) or 0)
        if hidden_size <= 0:
            hidden_size = int(getattr(model.config, "d_model", 0) or 0)
        if hidden_size <= 0:
            hidden_size = backend.hidden_size

        if getattr(tokenizer, "pad_token", None) is None and getattr(tokenizer, "eos_token", None):
            tokenizer.pad_token = tokenizer.eos_token

        num_params = 0
        try:
            num_params = sum(p.numel() for p in model.parameters())
        except Exception:  # pragma: no cover
            pass

        info = {
            "checkpoint": backend.checkpoint,
            "hidden_size": hidden_size,
            "num_parameters": num_params,
            "license": backend.license,
            "source_url": f"https://huggingface.co/{backend.checkpoint}",
            "is_fallback": backend.name != _DEFAULT_BACKEND_NAME,
            "device": device,
            "embedder_role": "feature_extractor",
            "embedder_backend": backend.name,
        }
        return _BackboneHandle(
            model=model,
            tokenizer=tokenizer,
            device=device,
            checkpoint=backend.checkpoint,
            hidden_size=hidden_size,
            info=info,
            backend=backend,
        )
    except Exception as exc:
        logger.warning("backend %s failed to load: %s", backend.name, exc)
        return None


def _try_load_random_mamba(backend: _EmbedderBackend, device: str) -> Optional[_BackboneHandle]:
    """Create a randomly-initialised Mamba-2 model for the falsifiability control."""
    try:
        import torch
        # Use the base Mamba2Model rather than the CausalLM variant: embed()
        # pulls ``last_hidden_state`` off the forward output, and the CausalLM
        # head replaces that with ``logits``, which would make this backend
        # raise on every call.
        from transformers import Mamba2Config, Mamba2Model

        logger.info("Creating random-mamba control model")
        config = Mamba2Config(
            hidden_size=backend.hidden_size,
            num_hidden_layers=24,
            state_size=128,
            conv_kernel=4,
            expand=2,
        )
        model = Mamba2Model(config)
        model.eval()
        model.to(device)

        # Random-init models have no tokenizer — create a dummy GPT-2 tokenizer
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(
            "gpt2", trust_remote_code=False,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        num_params = sum(p.numel() for p in model.parameters())
        info = {
            "checkpoint": "random-mamba-control",
            "hidden_size": backend.hidden_size,
            "num_parameters": num_params,
            "license": "n/a",
            "source_url": "(randomly-initialised)",
            "is_fallback": False,
            "device": device,
            "embedder_role": "feature_extractor",
            "embedder_backend": "random-mamba",
        }
        return _BackboneHandle(
            model=model,
            tokenizer=tokenizer,
            device=device,
            checkpoint="random-mamba",
            hidden_size=backend.hidden_size,
            info=info,
            backend=backend,
        )
    except Exception as exc:
        logger.warning("random-mamba creation failed: %s", exc)
        return None


def load_backbone(
    checkpoint: Optional[str] = None,
    device: Optional[str] = None,
) -> tuple[Any, Any]:
    """Load (or return cached) embedder backbone + tokenizer.

    Backend is selected via ``RC_EMBEDDER`` env (default: codestral-mamba).
    The ``checkpoint`` arg is retained for backward compat but ignored
    unless ``RC_EMBEDDER`` is not set and ``S2_SSM_CHECKPOINT`` is used.

    Returns a (model, tokenizer) tuple. Raises BackboneUnavailableError if
    every candidate fails.
    """
    global _HANDLE
    if _HANDLE is not None and _HANDLE.model is not None:
        return _HANDLE.model, _HANDLE.tokenizer

    with _LOAD_LOCK:
        if _HANDLE is not None and _HANDLE.model is not None:
            return _HANDLE.model, _HANDLE.tokenizer

        device = _resolve_device(device)

        # Legacy path: S2_SSM_CHECKPOINT set without RC_EMBEDDER
        legacy_ckpt = os.environ.get("S2_SSM_CHECKPOINT", "").strip()
        if legacy_ckpt and not os.environ.get("RC_EMBEDDER"):
            handle = _try_load_legacy(legacy_ckpt, device)
            if handle is not None:
                _HANDLE = handle
                BACKBONE_INFO.update(handle.info)
                logger.info(
                    "SSM backbone ready (legacy path): checkpoint=%s hidden=%d",
                    handle.checkpoint, handle.hidden_size,
                )
                return handle.model, handle.tokenizer

        # Modern path: RC_EMBEDDER-driven backend selection
        backend = _resolve_backend()
        handle = _try_load_backend(backend, device)
        if handle is not None:
            _HANDLE = handle
            BACKBONE_INFO.update(handle.info)
            logger.info(
                "Embedder ready: backend=%s checkpoint=%s hidden=%d params=%d device=%s",
                backend.name, handle.checkpoint,
                handle.hidden_size,
                handle.info.get("num_parameters", 0),
                handle.device,
            )
            return handle.model, handle.tokenizer

        # Fallback: try remaining backends in registry order
        for name, fb_backend in _BACKENDS.items():
            if name == backend.name:
                continue
            handle = _try_load_backend(fb_backend, device)
            if handle is not None:
                _HANDLE = handle
                BACKBONE_INFO.update(handle.info)
                logger.info(
                    "Embedder ready (fallback): backend=%s", fb_backend.name,
                )
                return handle.model, handle.tokenizer

        raise BackboneUnavailableError(
            "No embedder backend could be loaded. "
            "Tried RC_EMBEDDER={} and legacy fallbacks. "
            "Set RC_EMBEDDER to one of: {}.".format(
                os.environ.get("RC_EMBEDDER", _DEFAULT_BACKEND_NAME),
                ", ".join(_BACKENDS.keys()),
            )
        )


def _try_load_legacy(ckpt: str, device: str) -> Optional[_BackboneHandle]:
    """Load a legacy S2_SSM_CHECKPOINT model. Kept for backward compat."""
    try:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer
    except Exception:
        return None

    try:
        if ckpt not in _ALLOWED_CHECKPOINTS:
            raise BackboneUnavailableError(
                f"checkpoint {ckpt!r} is not in the allowlist"
            )
        revision = _PINNED_REVISIONS.get(ckpt, "main")
        tokenizer = AutoTokenizer.from_pretrained(
            ckpt, revision=revision, trust_remote_code=False,
        )
        model = AutoModel.from_pretrained(
            ckpt, revision=revision, trust_remote_code=False,
        )
        model.eval()
        try:
            model.to(device)
        except Exception:
            device = "cpu"
            model.to(device)
        hidden_size = int(getattr(model.config, "hidden_size", 0) or 0)
        if hidden_size <= 0:
            hidden_size = int(getattr(model.config, "d_model", 0) or 0)
        if getattr(tokenizer, "pad_token", None) is None and getattr(tokenizer, "eos_token", None):
            tokenizer.pad_token = tokenizer.eos_token
        info = {
            "checkpoint": ckpt,
            "hidden_size": hidden_size,
            "num_parameters": sum(p.numel() for p in model.parameters()),
            "license": "apache-2.0" if ckpt.startswith("state-spaces/") else "unknown",
            "source_url": f"https://huggingface.co/{ckpt}",
            "is_fallback": ckpt != DEFAULT_CHECKPOINT,
            "device": device,
            "embedder_role": "feature_extractor",
            "embedder_backend": "mamba-130m",
        }
        return _BackboneHandle(
            model=model, tokenizer=tokenizer, device=device,
            checkpoint=ckpt, hidden_size=hidden_size, info=info,
            backend=_BACKENDS.get("mamba-130m"),
        )
    except Exception as exc:
        logger.warning("legacy checkpoint %s failed: %s", ckpt, exc)
        return None


def get_handle() -> _BackboneHandle:
    """Return the loaded backbone handle. Loads on demand."""
    if _HANDLE is None:
        load_backbone()
    assert _HANDLE is not None  # for type-checkers
    return _HANDLE


def is_loaded() -> bool:
    return _HANDLE is not None and _HANDLE.model is not None


# --- Embedding ----------------------------------------------------------------

_EMBED_SEED = 0xC0DEC0DE & 0xFFFFFFFF


def embed(text: str) -> Any:
    """Return a 1D pooled embedding as a torch.Tensor.

    Pooling strategy depends on backend:
      - mean: mean over sequence dim (SSM backends)
      - cls:  first-token embedding (transformer backends: bge-code, unixcoder)
    """
    import torch

    handle = get_handle()
    model = handle.model
    tokenizer = handle.tokenizer
    device = handle.device
    backend = handle.backend

    max_len = 512
    pooling = "mean"
    if backend is not None:
        max_len = backend.max_seq_len
        pooling = backend.pooling

    text = text if text else " "
    torch.manual_seed(_EMBED_SEED)

    enc = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=max_len,
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
            out = model(input_ids=input_ids)

    last = getattr(out, "last_hidden_state", None)
    if last is None:
        last = out[0] if isinstance(out, (list, tuple)) else None
    if last is None:
        raise BackboneUnavailableError(
            "Backbone forward produced no last_hidden_state; cannot embed."
        )

    if pooling == "cls":
        # Use first token (CLS surrogate) for transformer models
        pooled = last[:, 0, :].squeeze(0)
    else:
        # Mean-pool over sequence dim -> [hidden_size]
        pooled = last.mean(dim=1).squeeze(0)
    return pooled.detach().cpu()


# --- AST-token bridge ---------------------------------------------------------

def ast_to_tokens(tree: Any, src: str) -> str:
    """Linearise an AST + source into a deterministic token string for the SSM."""
    if tree is None or getattr(tree, "root_node", None) is None:
        return src or ""

    src_bytes = src.encode("utf-8", errors="replace") if isinstance(src, str) else (src or b"")
    pieces: list[str] = []
    stack = [tree.root_node]
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
    "MAX_SEQ_LEN_CODESTRAL",
    "ast_to_tokens",
    "embed",
    "get_handle",
    "is_loaded",
    "load_backbone",
]
