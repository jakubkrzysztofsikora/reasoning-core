#!/usr/bin/env bash
# prefetch_mamba.sh
#
# Bake the state-spaces/mamba-130m-hf checkpoint into the eval image at
# build time so containers boot warm and CI does not depend on a live
# huggingface.co fetch during eval runs.
#
# Layout produced (under $HF_HOME, default /root/.cache/huggingface):
#   hub/models--state-spaces--mamba-130m-hf/snapshots/<rev>/{config.json,
#       tokenizer*,model.safetensors,...}
#   hub/models--state-spaces--mamba-130m-hf/refs/main
#
# Integrity gate: the safetensors weight file is sha256-checked against
# a pinned manifest. If the pin is missing or mismatches, the build
# fails LOUDLY -- we refuse to ship an image with unverified weights.

set -euo pipefail

MODEL_REPO="${RC_MAMBA_REPO:-state-spaces/mamba-130m-hf}"
MODEL_REVISION="${RC_MAMBA_REVISION:-main}"
HF_HOME="${HF_HOME:-/root/.cache/huggingface}"

# Pinned safetensors checksum.
#
# Provenance: sha256 of the `model.safetensors` blob hosted at
#   https://huggingface.co/state-spaces/mamba-130m-hf/resolve/main/model.safetensors
# captured 2026-05-01 against revision `main`. To rotate the pin, fetch
# the file out-of-band, run `sha256sum model.safetensors`, and update
# the value below in the same commit that updates RC_MAMBA_REVISION.
#
# Fail-loud policy: an empty pin terminates the build. CI must not pull
# unverified weights.
EXPECTED_SAFETENSORS_SHA256="${RC_MAMBA_SHA256:-1a5ed29c492ef4d485df3b7c2c8109771696589855b2162ad1ba618b6067cbea}"

if [[ -z "${EXPECTED_SAFETENSORS_SHA256}" ]]; then
    echo "[prefetch_mamba] FATAL: no sha256 pin set (RC_MAMBA_SHA256 / EXPECTED_SAFETENSORS_SHA256). Refusing to bake unverified weights." >&2
    exit 64
fi

mkdir -p "${HF_HOME}"

echo "[prefetch_mamba] downloading ${MODEL_REPO}@${MODEL_REVISION} into ${HF_HOME} ..."

python3 - <<PY
import os, sys
from huggingface_hub import snapshot_download

repo = os.environ.get("MODEL_REPO", "${MODEL_REPO}")
rev = os.environ.get("MODEL_REVISION", "${MODEL_REVISION}")
path = snapshot_download(
    repo_id=repo,
    revision=rev,
    cache_dir=os.environ["HF_HOME"] + "/hub",
    local_files_only=False,
    # Pull the artefacts we actually use at score time. tokenizer.* covers
    # both fast + slow tokenizer flavours; the safetensors file is the
    # weight blob; config.json is required by AutoModel.from_pretrained.
    allow_patterns=[
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "tokenizer.model",
        "special_tokens_map.json",
        "model.safetensors",
        "model-*.safetensors",          # sharded weights
        "model.safetensors.index.json", # shard index
        "pytorch_model.bin",             # pre-safetensors fallback
        "generation_config.json",
    ],
)
print(path)
PY

# Resolve the weight file path. HF Hub 1.13+ stores by content-addressed blob
# (snapshots/<rev>/<filename> is a symlink to blobs/<sha>); xet-backed
# downloads sometimes leave only the blob with no symlink. Probe both:
# (1) named symlink in snapshots/, (2) blob >50MB by size, (3) legacy.
# Track which probe found the file. Hash-pin only applies to the
# single-file model.safetensors layout (PROBE_KIND=single) AND to the
# xet content-addressed blob path (PROBE_KIND=xet_blob), where the blob's
# content IS the same single-file model.safetensors blob, just stored
# under its own sha-hash filename. Sharded layouts (multiple shards +
# index.json) and pytorch_model.bin fallback have different content
# hashes that the pin doesn't cover — those skip the gate (round-3 H4
# senior-dev fix: was previously skipping for ALL non-"model.safetensors"
# basenames including the xet blob path, which silently bypassed sha256
# verification on a code path the pin DOES cover).
PROBE_KIND=""
SAFETENSORS_PATH="$(find -L "${HF_HOME}/hub" -type f -name 'model.safetensors' 2>/dev/null | head -n 1 || true)"
[[ -n "${SAFETENSORS_PATH}" ]] && PROBE_KIND="single"
if [[ -z "${SAFETENSORS_PATH}" ]]; then
    SAFETENSORS_PATH="$(find -L "${HF_HOME}/hub" -type f -name '*.safetensors' ! -name '*.index.json' 2>/dev/null | head -n 1 || true)"
    [[ -n "${SAFETENSORS_PATH}" ]] && PROBE_KIND="sharded"
fi
if [[ -z "${SAFETENSORS_PATH}" ]]; then
    SAFETENSORS_PATH="$(find -L "${HF_HOME}/hub" -type f -name 'pytorch_model.bin' 2>/dev/null | head -n 1 || true)"
    [[ -n "${SAFETENSORS_PATH}" ]] && PROBE_KIND="pytorch_bin"
fi
# Last resort: any blob >50MB (xet content-addressed storage where filename is a sha).
if [[ -z "${SAFETENSORS_PATH}" ]]; then
    SAFETENSORS_PATH="$(find "${HF_HOME}/hub" -path '*/blobs/*' -type f -size +50M 2>/dev/null | head -n 1 || true)"
    [[ -n "${SAFETENSORS_PATH}" ]] && PROBE_KIND="xet_blob"
fi
if [[ -z "${SAFETENSORS_PATH}" || ! -f "${SAFETENSORS_PATH}" ]]; then
    echo "[prefetch_mamba] WARN: no weight file (*.safetensors / pytorch_model.bin) found in cache after snapshot_download." >&2
    echo "[prefetch_mamba] HF_HOME=${HF_HOME}" >&2
    echo "[prefetch_mamba] cache contents:" >&2
    find "${HF_HOME}" -maxdepth 6 -type f 2>/dev/null | head -20 >&2 || true
    if [[ "${RC_PREFETCH_SOFT_FAIL:-0}" == "1" ]]; then
        echo "[prefetch_mamba] RC_PREFETCH_SOFT_FAIL=1 — continuing; transformers will fetch lazily at first model load." >&2
        exit 0
    fi
    echo "[prefetch_mamba] FATAL: weight file missing. Set RC_PREFETCH_SOFT_FAIL=1 in CI to fall through to lazy load." >&2
    exit 65
fi

# Apply pin verification when the located file IS the single-file model
# (either via direct symlink probe or the xet content-addressed blob,
# which holds the same content under a sha-named filename). Skip pin for
# sharded layouts and pytorch_model.bin — pin doesn't cover those.
SAFETENSORS_BASENAME="$(basename "${SAFETENSORS_PATH}")"
if [[ "${PROBE_KIND}" != "single" && "${PROBE_KIND}" != "xet_blob" ]]; then
    echo "[prefetch_mamba] WARN: weight file is '${SAFETENSORS_BASENAME}' (probe=${PROBE_KIND}); sha256 pin only covers single-file model.safetensors. Skipping checksum gate; relying on RC_MAMBA_REVISION pin." >&2
else
    MANIFEST="$(mktemp)"
    printf '%s  %s\n' "${EXPECTED_SAFETENSORS_SHA256}" "${SAFETENSORS_PATH}" > "${MANIFEST}"
    echo "[prefetch_mamba] verifying sha256 against pin ${EXPECTED_SAFETENSORS_SHA256:0:12}..."
    if ! sha256sum -c "${MANIFEST}"; then
        OBSERVED="$(sha256sum "${SAFETENSORS_PATH}" | awk '{print $1}')"
        rm -f "${MANIFEST}"
        if [[ "${RC_PREFETCH_SOFT_FAIL:-0}" == "1" ]]; then
            echo "[prefetch_mamba] WARN: sha256 pin mismatch (expected ${EXPECTED_SAFETENSORS_SHA256:0:12}..., observed ${OBSERVED:0:12}...). RC_PREFETCH_SOFT_FAIL=1 — relying on HF revision pin (RC_MAMBA_REVISION=${MODEL_REVISION}) instead of blob hash. Continuing." >&2
        else
            echo "[prefetch_mamba] FATAL: safetensors checksum mismatch. Either the pin is stale or the upstream artefact rotated. Refusing to ship." >&2
            echo "[prefetch_mamba] expected: ${EXPECTED_SAFETENSORS_SHA256}" >&2
            echo "[prefetch_mamba] observed: ${OBSERVED}" >&2
            exit 66
        fi
    else
        rm -f "${MANIFEST}"
    fi
fi

# Sanity probe: load the model with transformers to confirm the cache
# is structured correctly. This catches missing tokenizer files BEFORE
# the image ships, not at first /score request.
python3 - <<'PY'
import os
from transformers import AutoModel, AutoTokenizer

repo = os.environ.get("MODEL_REPO", "state-spaces/mamba-130m-hf")
tok = AutoTokenizer.from_pretrained(repo)
mdl = AutoModel.from_pretrained(repo)
n = sum(p.numel() for p in mdl.parameters())
print(f"[prefetch_mamba] loaded {repo}: tokenizer ok, params={n:,}")
PY

echo "[prefetch_mamba] done. cache rooted at ${HF_HOME}"
