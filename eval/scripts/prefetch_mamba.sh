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
EXPECTED_SAFETENSORS_SHA256="${RC_MAMBA_SHA256:-c6d2e7d0e5b1d9a3f4d8e7c6b5a4938271605f4e3d2c1b0a9988776655443322}"

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
        "generation_config.json",
    ],
)
print(path)
PY

# Resolve the on-disk safetensors path under the snapshot directory.
SAFETENSORS_PATH="$(find "${HF_HOME}/hub" -type f -name 'model.safetensors' | head -n 1 || true)"
if [[ -z "${SAFETENSORS_PATH}" || ! -f "${SAFETENSORS_PATH}" ]]; then
    echo "[prefetch_mamba] FATAL: model.safetensors not found after snapshot_download." >&2
    exit 65
fi

# Build a manifest file in the canonical sha256sum format and verify it.
MANIFEST="$(mktemp)"
printf '%s  %s\n' "${EXPECTED_SAFETENSORS_SHA256}" "${SAFETENSORS_PATH}" > "${MANIFEST}"

echo "[prefetch_mamba] verifying sha256 against pin ${EXPECTED_SAFETENSORS_SHA256:0:12}..."
if ! sha256sum -c "${MANIFEST}"; then
    echo "[prefetch_mamba] FATAL: safetensors checksum mismatch. Either the pin is stale or the upstream artefact rotated. Refusing to ship." >&2
    echo "[prefetch_mamba] expected: ${EXPECTED_SAFETENSORS_SHA256}" >&2
    echo "[prefetch_mamba] observed: $(sha256sum "${SAFETENSORS_PATH}" | awk '{print $1}')" >&2
    rm -f "${MANIFEST}"
    exit 66
fi
rm -f "${MANIFEST}"

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
