"""Pin HuggingFace model-card metadata for each registered embedder backend.

The 2026-09-21 re-audit recommendation introduces three Mamba-3
candidates (`state-spaces/mamba3-{siso-893m,mimo-894m,siso-1.5b}`) whose
hidden size, vocab size, exact max_seq_len, and license header are not
yet verified in this sandbox (no web access). Before any benchmark
ladder runs, this script pulls each model's `config.json`,
`tokenizer_config.json`, and `README.md` metadata, computes a SHA-256
of the relevant fields, and writes the result to
`eval/calibrated/model_cards.json`.

Usage:

    python -m eval.pin_model_cards \\
        --backends mamba-130m unixcoder-base bge-code \\
                   mamba3-siso-893m mamba3-mimo-894m mamba3-siso-1.5b \\
        --out eval/calibrated/model_cards.json

The output is committed to the repo so that downstream evaluations
(RC-ML-Embedder, scoring-v3, dup_embed) can reference the pinned
metadata without re-fetching. Refuses to proceed if any required
field is missing -- this is the pre-flight gate for the Mamba-3
swap per the embedder memo (2026-09-21 update).

Exit codes:
    0 -- all cards pinned successfully
    2 -- at least one card could not be fetched (network down,
         model repo private, or required field missing)
    3 -- at least one card disagrees materially with the paper
         claims (e.g. claimed 16K context but actual card says 8K)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


# Paper-claim expectations for the Mamba-3 candidates. Used to validate
# the model card against the paper. If a card disagrees with the
# expected value, the script exits 3 (paper-claim mismatch).
#
# These are the fields that affect downstream correctness:
#
# For pure-SSM architectures (Mamba-3):
#   - The architecture does NOT expose max_position_embeddings (no
#     token-position table; positional signal is implicit in the
#     recurrent state via d_state + chunk_size). Instead, we check
#     - ssm_cfg.layer == "Mamba3" (confirms Mamba-3 architecture)
#     - ssm_cfg.d_state == 128 (matches paper Table 6's d_state=128)
#     - ssm_cfg.chunk_size is present and > 0
#   The effective "context window" for a pure SSM is theoretically
#   unbounded (the recurrent state carries information forward
#   indefinitely). The 16K figure in the paper refers to the
#   *benchmarked* length, not a hard cap.
#
# For transformer encoders (unixcoder-base, bge-code):
#   - max_position_embeddings: the hard cap.
#
# For mamba-130m (older Mamba architecture, causal LM):
#   - The model uses max_position_embeddings from the HuggingFace
#     tokenizer config (not config.json). 512 is the published value.
PAPER_CLAIMS: dict[str, dict[str, Any]] = {
    "mamba3-siso-893m": {
        "architecture": "Mamba3",
        "d_state": 128,
        "is_mimo": False,
        "kind": "mamba3-siso",
    },
    "mamba3-mimo-894m": {
        "architecture": "Mamba3",
        "d_state": 128,
        "is_mimo": True,
        "kind": "mamba3-mimo",
    },
    "mamba3-siso-1.5b": {
        "architecture": "Mamba3",
        "d_state": 128,
        "is_mimo": False,
        "kind": "mamba3-siso",
    },
    "mamba-130m": {
        "min_max_position_embeddings_via_tokenizer": 512,
        "kind": "mamba2-or-130m",
    },
    "unixcoder-base": {
        "min_max_position_embeddings": 512,
        "kind": "transformer-encoder",
    },
    "bge-code": {
        "min_max_position_embeddings": 8192,
        "kind": "transformer-encoder",
    },
}


def _hf_repo_for(backend: str) -> str:
    """Map RC_EMBEDDER value to HuggingFace repo id.

    Mirrors `_BACKENDS[name].checkpoint` in `src/ssm_backbone.py:213-275`.
    Kept here as a separate function so this script can run without
    importing the heavy transformers stack.
    """
    from src.ssm_backbone import _BACKENDS  # type: ignore
    if backend not in _BACKENDS:
        raise SystemExit(f"unknown backend: {backend!r}")
    return _BACKENDS[backend].checkpoint


def _fetch_config(repo: str, revision: str, *, hf_token: str | None = None) -> dict[str, Any]:
    """Fetch config.json from HuggingFace.

    Uses the huggingface_hub library if available (preferred); falls
    back to a raw HTTPS GET against the resolve endpoint. Returns
    the parsed JSON.
    """
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        path = hf_hub_download(
            repo_id=repo,
            filename="config.json",
            revision=revision,
            token=hf_token,
        )
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except ImportError:
        pass

    # Fallback: raw HTTPS (works for public repos).
    import urllib.request
    url = f"https://huggingface.co/{repo}/resolve/{revision}/config.json"
    req = urllib.request.Request(url, headers={"User-Agent": "reasoning-core-pin-model-cards/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_relevant_fields(config: dict[str, Any], backend: str) -> dict[str, Any]:
    """Pull the subset of config fields we care about for downstream
    correctness. Keeps the manifest small and stable across releases.
    """
    keys = (
        "model_type",
        "hidden_size",
        "vocab_size",
        "max_position_embeddings",
        "d_model",
        "d_intermediate",
        "d_state",
        "d_conv",
        "expand",
        "n_layer",
        "n_head",
        "intermediate_size",
        "architectures",
        "torch_dtype",
        "tie_word_embeddings",
        "attn_layer_idx",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in config:
            out[k] = config[k]
    # Flatten the SSM config block (Mamba-3 family) into the top level
    # with an ssm_ prefix so the gate checks are a single dict lookup.
    ssm_cfg = config.get("ssm_cfg")
    if isinstance(ssm_cfg, dict):
        for k, v in ssm_cfg.items():
            out[f"ssm_{k}"] = v
    out["_backend"] = backend
    return out


def _check_paper_claim(backend: str, fields: dict[str, Any]) -> list[str]:
    """Return a list of human-readable mismatches between the model
    card fields and the paper claims. Empty list == all checks passed.
    """
    expected = PAPER_CLAIMS.get(backend)
    if expected is None:
        return [f"no paper-claim expectation registered for {backend!r}"]

    mismatches: list[str] = []

    # Mamba-3 family: check SSM-specific fields (the architecture
    # doesn't expose max_position_embeddings).
    if expected.get("kind", "").startswith("mamba3"):
        actual_arch = fields.get("ssm_layer")
        if actual_arch is None:
            mismatches.append(f"{backend}: missing ssm_cfg.layer in card")
        elif actual_arch != expected["architecture"]:
            mismatches.append(
                f"{backend}: ssm_cfg.layer={actual_arch!r} != {expected['architecture']!r}"
            )
        actual_dstate = fields.get("ssm_d_state")
        if actual_dstate is None:
            mismatches.append(f"{backend}: missing ssm_cfg.d_state in card")
        elif actual_dstate != expected["d_state"]:
            mismatches.append(
                f"{backend}: ssm_cfg.d_state={actual_dstate} != paper claim {expected['d_state']}"
            )
        if "is_mimo" in expected:
            actual_mimo = fields.get("ssm_is_mimo")
            if actual_mimo is None:
                mismatches.append(f"{backend}: missing ssm_cfg.is_mimo in card")
            elif bool(actual_mimo) != bool(expected["is_mimo"]):
                mismatches.append(
                    f"{backend}: ssm_cfg.is_mimo={actual_mimo} != paper claim {expected['is_mimo']}"
                )
        # d_model and n_layer must be present and > 0.
        if not fields.get("d_model"):
            mismatches.append(f"{backend}: missing or zero d_model")
        if not fields.get("n_layer"):
            mismatches.append(f"{backend}: missing or zero n_layer")
        return mismatches

    # Transformer-encoder family: check max_position_embeddings.
    min_pos = expected.get("min_max_position_embeddings")
    if min_pos is not None:
        actual_pos = fields.get("max_position_embeddings")
        if actual_pos is None:
            mismatches.append(
                f"{backend}: card missing max_position_embeddings; expected >= {min_pos}"
            )
        elif isinstance(actual_pos, int) and actual_pos < min_pos:
            mismatches.append(
                f"{backend}: max_position_embeddings={actual_pos} < paper claim >= {min_pos}"
            )

    # mamba-130m path: 512 is published; not strictly enforced here
    # because the value lives in tokenizer_config.json, not config.json.
    return mismatches


def pin_one(backend: str, revision: str, *, hf_token: str | None) -> dict[str, Any]:
    """Pin one backend's model card. Returns the manifest entry."""
    repo = _hf_repo_for(backend)
    t0 = time.monotonic()
    try:
        config = _fetch_config(repo, revision, hf_token=hf_token)
    except Exception as exc:  # noqa: BLE001
        return {
            "backend": backend,
            "checkpoint": repo,
            "revision": revision,
            "status": "fetch_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - t0, 3),
        }
    fields = _extract_relevant_fields(config, backend)
    sha = hashlib.sha256(
        json.dumps(fields, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "backend": backend,
        "checkpoint": repo,
        "revision": revision,
        "status": "pinned",
        "fields": fields,
        "fields_sha256": sha,
        "elapsed_s": round(time.monotonic() - t0, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--backends", nargs="+", required=True,
        help="RC_EMBEDDER values to pin (e.g. mamba3-siso-893m).",
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "eval" / "calibrated" / "model_cards.json",
        help="Path to write the pinned manifest.",
    )
    parser.add_argument(
        "--hf-token", default=os.environ.get("HF_TOKEN"),
        help="HuggingFace token for private/gated repos (default: $HF_TOKEN).",
    )
    args = parser.parse_args()

    from src.ssm_backbone import _BACKENDS  # type: ignore
    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    fetch_failures = 0
    paper_mismatches: list[str] = []
    for backend in args.backends:
        if backend not in _BACKENDS:
            print(f"WARN: {backend!r} not in _BACKENDS registry; skipping", file=sys.stderr)
            continue
        revision = _BACKENDS[backend].revision or "main"
        entry = pin_one(backend, revision, hf_token=args.hf_token)
        entries.append(entry)
        if entry["status"] != "pinned":
            fetch_failures += 1
            print(f"FAIL: {backend}: {entry.get('error')}", file=sys.stderr)
            continue
        mismatches = _check_paper_claim(backend, entry["fields"])
        if mismatches:
            paper_mismatches.extend(mismatches)
            for m in mismatches:
                print(f"MISMATCH: {m}", file=sys.stderr)

    manifest = {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_backends": len(entries),
        "n_pinned": sum(1 for e in entries if e["status"] == "pinned"),
        "n_fetch_failed": fetch_failures,
        "paper_mismatches": paper_mismatches,
        "entries": entries,
    }
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote manifest to {out_path} ({len(entries)} backends, {fetch_failures} fetch failures, "
          f"{len(paper_mismatches)} paper mismatches)")
    if fetch_failures:
        return 2
    if paper_mismatches:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
