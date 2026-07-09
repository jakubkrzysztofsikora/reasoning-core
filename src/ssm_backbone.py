"""Multi-backend embedder loader for the System 2 sidecar.

This module owns the lifecycle of the embedder that powers the
architectural-impact / coherence scoring path. It is intentionally kept
small and side-effect-free at import time: the heavyweight `transformers`
import is deferred to `load_backbone()` so that `python3 -c "import
src.ssm_backbone"` stays cheap and offline-friendly.

Backends (selected via ``RC_EMBEDDER`` env):
    mamba-130m       -- default. state-spaces/mamba-130m-hf (Pile-LM, ~250 MB).
    codestral-mamba  -- opt-in. mistralai/Mamba-Codestral-7B-v0.1 (Apache 2.0,
                       code-pretrained Mamba-2, 256K context cap -> 8192,
                       ~14 GB fp16 / ~28 GB fp32). fp16 by default; see
                       ``RC_EMBEDDER_DTYPE``.
    bge-code         -- BAAI/bge-code-v1 (code-specialised transformer, ~4GB).
    unixcoder-base   -- microsoft/unixcoder-base (code transformer baseline).
    random-mamba     -- randomly-initialised Mamba-2 control for falsifiability.

Dtype env (memory control):
    RC_EMBEDDER_DTYPE  -- float32 | float16 | bfloat16 | auto. Unset →
                          codestral-mamba defaults to float16 (memory saver
                          on the 7B model); other backends default to
                          transformers' native dtype (fp32).

Legacy env (backward compat):
    S2_SSM_CHECKPOINT  -- override for mamba-130m path ONLY.
    S2_DEVICE          -- "cpu" (default) or "cuda".
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class _OfflineTokenizer:
    """Deterministic whitespace tokenizer for the random-mamba control.

    The random-mamba embedder is intentionally a random-weights control, so the
    exact tokenization does not affect its falsifiability role. Using a local
    tokenizer removes the prior dependency on downloading ``gpt2`` from
    HuggingFace and makes the control runnable on air-gapped machines.
    """

    def __init__(self, vocab_size: int = 50000) -> None:
        self.vocab_size = vocab_size
        self.pad_token = "[PAD]"
        self.eos_token = "[EOS]"
        self.unk_token = "[UNK]"
        self.pad_token_id = 0
        self.eos_token_id = 1
        self.unk_token_id = 2
        self._special = {
            self.pad_token: self.pad_token_id,
            self.eos_token: self.eos_token_id,
            self.unk_token: self.unk_token_id,
        }

    def _token_id(self, token: str) -> int:
        if token in self._special:
            return self._special[token]
        h = hash(token) & 0xFFFFFFFF
        return 3 + (h % (self.vocab_size - 3))

    def __call__(
        self,
        text: str,
        *,
        return_tensors: Optional[str] = None,
        truncation: bool = False,
        max_length: int = 512,
        padding: bool = False,
    ) -> dict[str, Any]:
        tokens = (text or "").split()
        input_ids = [self.eos_token_id] + [self._token_id(t) for t in tokens] + [self.eos_token_id]
        if truncation and max_length and len(input_ids) > max_length:
            input_ids = input_ids[:max_length]
        if padding:
            # Padding is not used by the embedder path, but implement the contract.
            input_ids = input_ids + [self.pad_token_id] * max(0, max_length - len(input_ids))
        attention_mask = [1 if tid != self.pad_token_id else 0 for tid in input_ids]
        if return_tensors == "pt":
            try:
                import torch

                return {
                    "input_ids": torch.tensor([input_ids], dtype=torch.long),
                    "attention_mask": torch.tensor([attention_mask], dtype=torch.long),
                }
            except ImportError:
                pass
        return {"input_ids": input_ids, "attention_mask": attention_mask}


class _TorchFreeEmbedding:
    """Minimal tensor-like object returned by the torch-free embed path."""

    def __init__(self, values: list[float]) -> None:
        self._values = values
        self.shape = (len(values),)

    def dim(self) -> int:
        return 1

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, idx):
        if isinstance(idx, int):
            return self._values[idx]
        raise NotImplementedError

    def tolist(self) -> list[float]:
        return list(self._values)

    def detach(self):
        return self

    def cpu(self):
        return self

    def to(self, _device):
        return self


class _TorchFreeRandomMamba:
    """Deterministic random embedder for the random-mamba control without torch.

    Produces reproducible mean-pooled embeddings from token IDs using Python's
    stdlib random module seeded per token. This lets the random-mamba backend
    load and embed on machines that lack PyTorch/Transformers, which is exactly
    the air-gapped/test environment the control is meant to exercise.
    """

    def __init__(self, hidden_size: int, vocab_size: int = 50000) -> None:
        self.hidden_size = hidden_size
        self.vocab_size = vocab_size

    def __call__(self, *, input_ids, attention_mask=None, **_kwargs) -> Any:
        import random

        # input_ids may be a nested list or a torch tensor.
        if hasattr(input_ids, "tolist"):
            batch = input_ids.tolist()
        else:
            batch = input_ids
        if not isinstance(batch, list) or not batch:
            batch = [[]]
        if not isinstance(batch[0], list):
            batch = [batch]

        # Mean-pool over the sequence dimension for each batch item.
        pooled_batch: list[list[float]] = []
        for seq in batch:
            token_vectors: list[list[float]] = []
            for tid in seq:
                rng = random.Random((tid + 1) * 7919 + _EMBED_SEED)
                vec = [rng.gauss(0.0, 0.02) for _ in range(self.hidden_size)]
                token_vectors.append(vec)
            if token_vectors:
                pooled = [sum(col) / len(token_vectors) for col in zip(*token_vectors)]
            else:
                pooled = [0.0] * self.hidden_size
            pooled_batch.append(pooled)

        # Return the first (and usually only) pooled vector wrapped in a
        # tensor-like object. For multi-item batches callers can extend this.
        return type("Out", (), {"pooled_embeddings": _TorchFreeEmbedding(pooled_batch[0])})()

    def eval(self) -> "_TorchFreeRandomMamba":
        return self

    def to(self, _device) -> "_TorchFreeRandomMamba":
        return self

    def parameters(self):
        return iter([])


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
    # Quantized Codestral-Mamba via llama.cpp / GGUF. Keeps the code
    # pretraining at a fraction of the RAM (~2.5 GB for Q2_K vs ~14 GB
    # for the fp16 HF weights). Loaded via llama-cpp-python, not HF
    # transformers — see `_try_load_gguf_backend` and the dispatch in
    # `embed()`. Default file is Q2_K; override with `RC_CODESTRAL_GGUF_FILE`.
    "codestral-mamba-gguf": _EmbedderBackend(
        name="codestral-mamba-gguf",
        checkpoint="gabriellarson/Mamba-Codestral-7B-v0.1-GGUF",
        pooling="mean",   # informational; GGUF returns a pre-pooled embedding
        max_seq_len=8192,
        hidden_size=4096,
        revision="main",  # operator pins via RC_<SLUG>_REVISION
        license="apache-2.0",
    ),
}

# Default to a backend with a real SHA pin so a fresh install works without
# operator-supplied revision overrides. ``codestral-mamba`` / ``bge-code`` /
# ``unixcoder-base`` carry ``revision="main"`` in the registry and are
# fail-closed under ``_resolve_revision_for_backend`` until an operator pins
# them (see _PINNED_REVISIONS or RC_<REPO_SLUG>_REVISION).
_DEFAULT_BACKEND_NAME: str = "mamba-130m"

# Legacy constants — kept for API compat; new code uses _EmbedderBackend.
DEFAULT_CHECKPOINT = _BACKENDS["mamba-130m"].checkpoint
FALLBACK_CHECKPOINTS = (
    "state-spaces/mamba2-130m",
    "sshleifer/tiny-gpt2",
)
# Allowlist of HF repos the loader is willing to instantiate. The new
# ``RC_EMBEDDER`` backends widen this set explicitly -- ``_try_load_backend``
# refuses to call ``AutoModel.from_pretrained`` on any checkpoint outside.
# ``__random_mamba__`` is the in-process control (no remote artifact).
_ALLOWED_CHECKPOINTS = frozenset({
    DEFAULT_CHECKPOINT,
    *FALLBACK_CHECKPOINTS,
    "mistralai/Mamba-Codestral-7B-v0.1",
    "gabriellarson/Mamba-Codestral-7B-v0.1-GGUF",
    "BAAI/bge-code-v1",
    "microsoft/unixcoder-base",
    "__random_mamba__",
})
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

# Negative cache for backbone load failures. Without this, every call to
# load_backbone() that fails (e.g. broken GGUF, upstream llama.cpp bug)
# re-attempts the full load -- including a ~4 GB mmap of the GGUF file --
# which compounds into severe memory pressure under request load
# (2026-05-17 incident). The cooldown is configurable via env.
_LAST_FAILURE_TS: float = 0.0
_LAST_FAILURE_MSG: str = ""
_DEFAULT_FAIL_COOLDOWN_S: float = 60.0


def _fail_cooldown_s() -> float:
    raw = os.environ.get("S2_BACKBONE_FAIL_COOLDOWN_S")
    if raw is None or raw == "":
        return _DEFAULT_FAIL_COOLDOWN_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_FAIL_COOLDOWN_S


def reset_failure_cache() -> None:
    """Clear the cached load failure. Used by tests and operators."""
    global _LAST_FAILURE_TS, _LAST_FAILURE_MSG
    _LAST_FAILURE_TS = 0.0
    _LAST_FAILURE_MSG = ""


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
    """Return a 40-char commit SHA for ``backend``, or ``None`` when the
    backend has no remote artifact (``random-mamba``).

    Fail-closed on mutable refs (``"main"``, branch/tag names) and on backends
    whose registry entry doesn't carry a pin. Operators can override per
    repo via ``RC_<REPO_SLUG>_REVISION=<40-hex-SHA>``; the override itself
    is also validated as a SHA. This mirrors ``_resolve_revision`` and
    restores the supply-chain hardening shipped in ``bc536c1`` for the new
    ``RC_EMBEDDER`` registry.

    To enable a new backend that ships with ``revision="main"`` in the
    registry, either edit the registry to a pinned SHA or export
    ``RC_<REPO_SLUG>_REVISION=<sha>`` in the deploy environment.
    """
    if backend.name == "random-mamba":
        return None

    env_key = _revision_env_key(backend.checkpoint)
    override = os.environ.get(env_key, "").strip()
    if override:
        if not _SHA_RE.match(override):
            raise BackboneUnavailableError(
                f"{env_key}={override!r} is not a 40-char hex commit SHA. "
                f"Mutable refs (branches/tags) are forbidden."
            )
        return override

    # Prefer the centralized pin table; backends still listed there resolve
    # without registry duplication.
    pin = _PINNED_REVISIONS.get(backend.checkpoint)
    if pin:
        return pin

    # Registry-level pin -- only accepted if it's a real SHA. Mutable refs
    # like ``"main"`` are explicitly rejected so a malicious upstream push
    # cannot reach a fresh sidecar.
    rev = (backend.revision or "").strip()
    if _SHA_RE.match(rev):
        return rev

    raise BackboneUnavailableError(
        f"backend {backend.name!r} (checkpoint={backend.checkpoint!r}) "
        f"has no pinned revision. revision={rev!r} is not a 40-char hex SHA. "
        f"Pin it in _PINNED_REVISIONS, or set "
        f"{env_key}=<sha> to unblock at deploy time."
    )


# --- Loader -------------------------------------------------------------------


_VALID_DTYPES = frozenset({"float32", "float16", "bfloat16", "auto"})


def _resolve_dtype(backend: _EmbedderBackend) -> Any:
    """Return the torch.dtype (or sentinel) to pass to ``from_pretrained``.

    Resolution order:
      1. ``RC_EMBEDDER_DTYPE`` env (validated against ``_VALID_DTYPES``).
      2. Per-backend memory-saver default: ``codestral-mamba`` → ``float16``
         (the 7B checkpoint is ~28 GB at fp32 and OOMs many laptops).
      3. ``None`` → transformers' native dtype (fp32).

    Returns ``"auto"`` (string) when the user explicitly asked for HF's
    auto-resolution; otherwise a ``torch.dtype`` or ``None``.
    """
    import torch  # local; caller already imports torch upstream

    raw = os.environ.get("RC_EMBEDDER_DTYPE", "").strip().lower()
    if raw and raw not in _VALID_DTYPES:
        raise BackboneUnavailableError(
            f"RC_EMBEDDER_DTYPE={raw!r} invalid. "
            f"Expected one of: {sorted(_VALID_DTYPES)}."
        )
    if raw == "float32":
        return torch.float32
    if raw == "float16":
        return torch.float16
    if raw == "bfloat16":
        return torch.bfloat16
    if raw == "auto":
        return "auto"
    # Unset → per-backend default.
    if backend.name == "codestral-mamba":
        return torch.float16
    return None


_GGUF_DEFAULT_FILE = "Mamba-Codestral-7B-v0.1-Q2_K.gguf"


def _try_load_gguf_backend(backend: _EmbedderBackend, device: str) -> Optional[_BackboneHandle]:
    """Load a GGUF embedder via ``llama-cpp-python``.

    Downloads the GGUF artifact from HuggingFace (default Q2_K, override
    with ``RC_CODESTRAL_GGUF_FILE``) and instantiates ``llama_cpp.Llama``
    in embedding mode. The handle's ``model`` is a thin adapter that the
    GGUF branch in ``embed()`` keys on by backend name.
    """
    try:
        from huggingface_hub import hf_hub_download
        import llama_cpp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.error("llama-cpp-python or huggingface_hub import failed: %s", exc)
        return None

    revision = _resolve_revision_for_backend(backend)
    filename = os.environ.get("RC_CODESTRAL_GGUF_FILE", _GGUF_DEFAULT_FILE)

    logger.info(
        "Loading GGUF embedder backend=%s repo=%s file=%s revision=%s",
        backend.name, backend.checkpoint, filename, (revision or "latest")[:12],
    )
    try:
        gguf_path = hf_hub_download(
            repo_id=backend.checkpoint,
            filename=filename,
            revision=revision,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GGUF download failed for %s/%s: %s", backend.checkpoint, filename, exc)
        return None

    # n_ctx caps SSM activation/state buffers. On a 16 GiB host the Mamba-2
    # forward pass at 8192 peaks ~18 GiB and trips the watchdog; 2048 is plenty
    # for a code diff (avg ~600 tokens). Override via RC_GGUF_N_CTX.
    n_ctx = int(os.environ.get("RC_GGUF_N_CTX", "0")) or (backend.max_seq_len or 8192)
    # n_batch / n_ubatch dominate llama.cpp compute-buffer allocation for
    # Mamba-2 (each scales the SSM scan workspace). On a 16 GiB host the default
    # n_batch=512 blows peak RSS to ~20 GiB. Cap small; embedding-only use never
    # benefits from large batches anyway.
    n_batch = int(os.environ.get("RC_GGUF_N_BATCH", "128"))
    n_ubatch = int(os.environ.get("RC_GGUF_N_UBATCH", str(min(n_batch, 64))))
    try:
        llama = llama_cpp.Llama(
            model_path=gguf_path,
            embedding=True,
            n_ctx=n_ctx,
            n_batch=n_batch,
            n_ubatch=n_ubatch,
            n_threads=int(os.environ.get("RC_GGUF_THREADS", "0")) or None,
            verbose=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("llama_cpp.Llama init failed: %s", exc)
        return None

    hidden = int(llama.n_embd()) if hasattr(llama, "n_embd") else backend.hidden_size
    info = {
        "checkpoint": backend.checkpoint,
        "hidden_size": hidden,
        "num_parameters": 7_151_185_920,  # Codestral-Mamba-7B
        "license": backend.license,
        "source_url": f"https://huggingface.co/{backend.checkpoint}",
        "is_fallback": backend.name != _DEFAULT_BACKEND_NAME,
        "device": "cpu",
        "embedder_role": "feature_extractor",
        "embedder_backend": backend.name,
        "gguf_file": filename,
        "gguf_path": gguf_path,
    }
    return _BackboneHandle(
        model=llama,            # raw Llama instance; embed() dispatches on backend.name
        tokenizer=None,         # GGUF tokenizer is inside Llama, accessed via embed()
        device="cpu",
        checkpoint=backend.checkpoint,
        hidden_size=hidden,
        info=info,
        backend=backend,
    )


def _try_load_backend(backend: _EmbedderBackend, device: str) -> Optional[_BackboneHandle]:
    """Attempt to load a single backend. Returns None on failure."""
    # The random-mamba control can run without PyTorch/Transformers using a
    # torch-free deterministic embedder, so skip the import requirement for it.
    if backend.name == "random-mamba":
        return _try_load_random_mamba(backend, device)

    try:
        import torch  # noqa: F401
        from transformers import AutoModel, AutoTokenizer
    except Exception as exc:  # pragma: no cover
        logger.error("transformers/torch import failed: %s", exc)
        return None

    try:
        # Defense-in-depth: refuse to instantiate any HF repo that isn't on
        # the allowlist, even if it somehow landed in the backend registry.
        # Closes the gap where adding a ``_BACKENDS`` entry would implicitly
        # widen the trust boundary.
        if backend.checkpoint not in _ALLOWED_CHECKPOINTS:
            raise BackboneUnavailableError(
                f"backend {backend.name!r} checkpoint {backend.checkpoint!r} "
                f"is not in _ALLOWED_CHECKPOINTS; refusing to load."
            )
        if backend.name == "codestral-mamba-gguf":
            return _try_load_gguf_backend(backend, device)

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
        dtype = _resolve_dtype(backend)
        model_kwargs = dict(load_kwargs)
        if dtype is not None:
            model_kwargs["torch_dtype"] = dtype
        model_kwargs["low_cpu_mem_usage"] = True
        logger.info(
            "Loading embedder weights dtype=%s low_cpu_mem_usage=True",
            getattr(dtype, "__name__", None) or str(dtype) or "native",
        )
        model = AutoModel.from_pretrained(
            backend.checkpoint, **model_kwargs,
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
    """Create a randomly-initialised Mamba-2 model for the falsifiability control.

    Falls back to a torch-free deterministic embedder when PyTorch is not
    installed, so the control remains usable in air-gapped / CI environments.
    """
    try:
        import torch  # noqa: F401
        from transformers import Mamba2Config, Mamba2Model
    except ImportError:
        logger.info("random-mamba: torch/transformers unavailable, using torch-free control")
        tokenizer: Any = _OfflineTokenizer(vocab_size=50000)
        model = _TorchFreeRandomMamba(hidden_size=backend.hidden_size)
        info = {
            "checkpoint": "random-mamba-control",
            "hidden_size": backend.hidden_size,
            "num_parameters": 0,
            "license": "n/a",
            "source_url": "(randomly-initialised, torch-free)",
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

    try:
        # Use the base Mamba2Model rather than the CausalLM variant: embed()
        # pulls ``last_hidden_state`` off the forward output, and the CausalLM
        # head replaces that with ``logits``, which would make this backend
        # raise on every call.
        logger.info("Creating random-mamba control model")
        config = Mamba2Config(
            hidden_size=backend.hidden_size,
            num_hidden_layers=24,
            state_size=128,
            conv_kernel=4,
            expand=2,
            num_heads=24,
            head_dim=64,
            vocab_size=50000,
        )
        model = Mamba2Model(config)
        model.eval()
        model.to(device)

        # Random-init models have no tokenizer. Prefer a fully offline
        # whitespace tokenizer so the control runs without network credentials.
        # Fall back to GPT-2 only when it is already cached, to preserve
        # backward-compatible behaviour in warm environments.
        tokenizer = _OfflineTokenizer(vocab_size=50000)
        offline = (
            os.environ.get("TRANSFORMERS_OFFLINE") == "1"
            or os.environ.get("HF_HUB_OFFLINE") == "1"
        )
        if not offline:
            try:
                from transformers import AutoTokenizer

                gpt2 = AutoTokenizer.from_pretrained("gpt2", trust_remote_code=False)
                if gpt2.pad_token is None:
                    gpt2.pad_token = gpt2.eos_token
                tokenizer = gpt2
            except Exception:
                logger.info("random-mamba: using offline tokenizer (gpt2 not cached)")

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
    global _HANDLE, _LAST_FAILURE_TS, _LAST_FAILURE_MSG
    if _HANDLE is not None and _HANDLE.model is not None:
        return _HANDLE.model, _HANDLE.tokenizer

    # Negative cache: if a recent load failed, don't retry within the cooldown.
    # Each failed attempt mmaps multi-GB model files and leaks file handles
    # via llama_cpp.Llama's __del__ AttributeError, so retrying on every
    # request rapidly fills swap.
    cooldown = _fail_cooldown_s()
    if cooldown > 0 and _LAST_FAILURE_TS > 0:
        elapsed = time.monotonic() - _LAST_FAILURE_TS
        if elapsed < cooldown:
            raise BackboneUnavailableError(
                f"{_LAST_FAILURE_MSG} (cached failure, retry in "
                f"{cooldown - elapsed:.0f}s)"
            )

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
                _LAST_FAILURE_TS = 0.0
                _LAST_FAILURE_MSG = ""
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
            _LAST_FAILURE_TS = 0.0
            _LAST_FAILURE_MSG = ""
            BACKBONE_INFO.update(handle.info)
            logger.info(
                "Embedder ready: backend=%s checkpoint=%s hidden=%d params=%d device=%s",
                backend.name, handle.checkpoint,
                handle.hidden_size,
                handle.info.get("num_parameters", 0),
                handle.device,
            )
            return handle.model, handle.tokenizer

        # If the operator explicitly set RC_EMBEDDER, honor it strictly: do
        # NOT silently fall back to a heavier backend. The fallback loop used
        # to iterate _BACKENDS in dict order, which put the 14 GB full-HF
        # ``codestral-mamba`` first -- so any transient GGUF failure
        # (download flake, revision mismatch, llama_cpp issue) silently
        # promoted the load into the 76 GB swap-thrash territory that crashed
        # the host on 2026-05-16. Strict mode short-circuits that path.
        operator_explicit = bool(os.environ.get("RC_EMBEDDER", "").strip())
        if operator_explicit:
            msg = (
                f"RC_EMBEDDER={backend.name} failed to load and no fallback "
                "is permitted when the operator pinned the backend explicitly. "
                "Unset RC_EMBEDDER to allow registry fallback."
            )
            _LAST_FAILURE_TS = time.monotonic()
            _LAST_FAILURE_MSG = msg
            raise BackboneUnavailableError(msg)

        # Fallback: only runs when the operator did NOT pin RC_EMBEDDER.
        # Tries remaining backends in registry order, smallest-first to keep
        # an accidental fallback from blowing up RAM.
        fallback_order = sorted(
            _BACKENDS.items(),
            key=lambda kv: 0 if kv[1].hidden_size <= 1024 else kv[1].hidden_size,
        )
        for name, fb_backend in fallback_order:
            if name == backend.name:
                continue
            handle = _try_load_backend(fb_backend, device)
            if handle is not None:
                _HANDLE = handle
                _LAST_FAILURE_TS = 0.0
                _LAST_FAILURE_MSG = ""
                BACKBONE_INFO.update(handle.info)
                logger.info(
                    "Embedder ready (fallback): backend=%s", fb_backend.name,
                )
                return handle.model, handle.tokenizer

        msg = (
            "No embedder backend could be loaded. "
            "Tried RC_EMBEDDER={} and legacy fallbacks. "
            "Set RC_EMBEDDER to one of: {}.".format(
                os.environ.get("RC_EMBEDDER", _DEFAULT_BACKEND_NAME),
                ", ".join(_BACKENDS.keys()),
            )
        )
        _LAST_FAILURE_TS = time.monotonic()
        _LAST_FAILURE_MSG = msg
        raise BackboneUnavailableError(msg)


def _try_load_legacy(ckpt: str, device: str) -> Optional[_BackboneHandle]:
    """Load a legacy ``S2_SSM_CHECKPOINT`` model. Kept for the
    pre-``RC_EMBEDDER`` path and for ``test_security_hardening``.

    Resolution is strict: ``_resolve_revision`` raises if there is no SHA pin
    in ``_PINNED_REVISIONS`` (and rejects mutable refs via env override).
    Metadata reflects the actual checkpoint loaded -- the previous version
    hard-coded ``embedder_backend="mamba-130m"`` regardless of ``ckpt``, which
    made ``/health`` and audit rows lie about which model was running.
    """
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
        # Strict SHA pin -- fails closed on missing/mutable refs.
        revision = _resolve_revision(ckpt)
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
        # Match the loaded checkpoint to a registry entry when possible so
        # /health metadata is accurate; otherwise tag the checkpoint name
        # itself rather than lying about the backend.
        backend_match = next(
            (b for b in _BACKENDS.values() if b.checkpoint == ckpt),
            None,
        )
        embedder_backend = backend_match.name if backend_match else ckpt
        info = {
            "checkpoint": ckpt,
            "hidden_size": hidden_size,
            "num_parameters": sum(p.numel() for p in model.parameters()),
            "license": (
                backend_match.license if backend_match
                else ("apache-2.0" if ckpt.startswith("state-spaces/") else "unknown")
            ),
            "source_url": f"https://huggingface.co/{ckpt}",
            "is_fallback": ckpt != DEFAULT_CHECKPOINT,
            "device": device,
            "embedder_role": "feature_extractor",
            "embedder_backend": embedder_backend,
        }
        return _BackboneHandle(
            model=model, tokenizer=tokenizer, device=device,
            checkpoint=ckpt, hidden_size=hidden_size, info=info,
            backend=backend_match,
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
    """Return a 1D pooled embedding.

    Pooling strategy depends on backend:
      - mean: mean over sequence dim (SSM backends)
      - cls:  first-token embedding (transformer backends: bge-code, unixcoder)
      - GGUF backends (``codestral-mamba-gguf``) return a pre-pooled embedding
        from llama.cpp and bypass the tokenize/forward/pool path entirely.

    When PyTorch is unavailable and the backend is the torch-free
    random-mamba control, returns a ``_TensorLike`` with shape ``(hidden_size,)``.
    """
    handle = get_handle()
    model = handle.model
    tokenizer = handle.tokenizer
    device = handle.device
    backend = handle.backend

    # Torch-free path: random-mamba control without PyTorch installed.
    if isinstance(model, _TorchFreeRandomMamba):
        max_len = backend.max_seq_len if backend is not None else 512
        enc = tokenizer(
            text or " ",
            return_tensors=None,
            truncation=True,
            max_length=max_len,
            padding=False,
        )
        input_ids = enc["input_ids"]
        out = model(input_ids=input_ids)
        return out.pooled_embeddings

    import torch

    # GGUF path: llama.cpp's .embed() returns either a flat [hidden] list
    # (when the model GGUF declares a pooling type) or a per-token
    # [n_tokens, hidden] list (Mamba-Codestral GGUFs do NOT declare pooling,
    # so we get per-token). Detect 2D and mean-pool to [hidden] so the
    # downstream cosine/L2 metrics get a stable shape independent of input
    # token count. Failure to pool here surfaces as
    # "inconsistent tensor size" in _cosine_similarity when two inputs of
    # different lengths are compared.
    if backend is not None and backend.name == "codestral-mamba-gguf":
        text = text if text else " "
        try:
            vec = model.embed(text)  # llama_cpp.Llama instance
        except Exception as exc:  # noqa: BLE001
            raise BackboneUnavailableError(f"GGUF embed failed: {exc}") from exc
        t = torch.tensor(vec, dtype=torch.float32)
        if t.dim() == 2:
            t = t.mean(dim=0)
        elif t.dim() > 2:
            t = t.reshape(-1, t.shape[-1]).mean(dim=0)
        return t

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
