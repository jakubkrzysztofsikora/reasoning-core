# syntax=docker/dockerfile:1.7
#
# reasoning-core eval image.
#
# Build size budget: <5 GB final.
#   Approx: python:3.11-slim (~150 MB) + torch CPU wheel (~750 MB) +
#           transformers + tokenizer deps (~250 MB) + tree-sitter grammars
#           (~30 MB) + scipy/statsmodels (~120 MB) + Mamba-130m safetensors
#           (~250 MB) + repo source (~5 MB)  ==>  ~1.6 GB compressed,
#           ~2.1 GB on disk uncompressed. Headroom: ~2.9 GB.
#
# Cold build time on a GitHub-hosted runner: ~6 min (torch + checkpoint).
# Warm build with layer cache: <90 s.
#
# Two stages:
#   builder  -- apt deps + venv with all wheels + pre-fetched HF cache.
#   runtime  -- copies the venv + HF cache + project source only. No apt
#               toolchain in the runtime layer.
#
# Invocation contract:
#   ENTRYPOINT ["/bin/bash"]
#   so CI calls e.g.:
#     docker run --rm reasoning-core-eval:VERSION \
#       -lc 'eval/run_suite.py --n 5 --arms vanilla,treatment'
#
#   Sidecar port 8765 is EXPOSED but bound to 127.0.0.1 inside the
#   container by `scripts/start-sidecar.sh` -- no external NIC reach.

############################
# Stage 1: builder
############################
FROM python:3.11-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        jq \
        ca-certificates \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv keeps the runtime stage copy small and reproducible.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Pinned runtime + eval deps. requirements.txt is the production set;
# requirements-eval.txt (Track B / RC-209) layers on scipy/statsmodels/
# datasets/radon. The image is built from the project root so both files
# resolve relative to the build context.
COPY requirements.txt /tmp/requirements.txt
COPY eval/requirements-eval.txt /tmp/requirements-eval.txt

RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt \
    && pip install -r /tmp/requirements-eval.txt

# Bake the Mamba checkpoint into the image so containers boot warm and
# CI does not depend on Hugging Face availability mid-run. The script
# verifies a sha256 manifest and aborts the build if the pin drifts.
ENV HF_HOME=/root/.cache/huggingface
COPY eval/scripts/prefetch_mamba.sh /tmp/prefetch_mamba.sh
RUN chmod +x /tmp/prefetch_mamba.sh && /tmp/prefetch_mamba.sh

############################
# Stage 2: runtime
############################
FROM python:3.11-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Minimal runtime apt set. git stays for repo clones during eval; jq
# helps the run scripts parse hook payloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        jq \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Bring the prepared venv and the warmed HF cache forward.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface

ENV PATH="/opt/venv/bin:${PATH}" \
    HF_HOME=/root/.cache/huggingface \
    S2_FAIL_CLOSED=1 \
    S2_TIMEOUT=60 \
    TRANSFORMERS_OFFLINE=0 \
    TORCH_NUM_THREADS=2 \
    PYTHONPATH=/app

# Project source. The .dockerignore (sibling of this Dockerfile) keeps
# eval/results/, .venv/, __pycache__/, and large local artefacts out of
# the layer.
WORKDIR /app
COPY . /app

# Sidecar HTTP port. Bound to 127.0.0.1 inside the container by
# scripts/start-sidecar.sh; EXPOSE here is documentation only.
EXPOSE 8765

# Bash entrypoint so CI / operators pass the actual command:
#   docker run --rm IMAGE -lc 'python3 eval/run_suite.py --n 5 ...'
ENTRYPOINT ["/bin/bash"]
