# Dukaan Saathi — single-container GPU Space (HF Spaces, sdk: docker).
# Runs the llama.cpp `llama-server` (Gemma-4-12B Q4_K_M + vision mmproj) AND the
# Gradio app (which also loads faster-whisper STT + Veena TTS) in one container.
#
# Base = official prebuilt llama.cpp CUDA server image (CUDA 12.8.1) → we get the
# maintained `llama-server` binary + matching CUDA runtime for free, then layer the
# Python app on top. Target hardware: 1× L4 (24 GB VRAM, 400 GB disk, $0.80/hr).
#
# VERIFY on first build: (1) exact Q4_K_M filename in ggml-org/gemma-4-12B-it-GGUF;
# (2) VRAM fit (Gemma Q4 ~7GB + Whisper ~3GB + Veena bf16 ~6GB ≈ 18-19GB) — if it
# OOMs, set DUKAAN_VEENA_4BIT=true (add bitsandbytes+accelerate) or DUKAAN_WHISPER_MODEL=medium;
# (3) faster-whisper finds cuDNN (handled via LD_LIBRARY_PATH in the entrypoint).
FROM ghcr.io/ggml-org/llama.cpp:server-cuda

# --- system tools + a non-root user (HF Spaces runs the container as uid 1000)
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*
# The llama.cpp base image already has a UID-1000 user; -o lets us add our named
# "user" sharing that UID (HF Spaces runs as uid 1000) instead of failing.
RUN useradd -m -u 1000 -o user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

# --- uv (standalone installer; manages its own Python + venv)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# --- Python deps (sync the locked env; source copied first so hatchling can build
#     the `dukaan` package as part of `uv sync`).
COPY --chown=user pyproject.toml uv.lock README.md ./
COPY --chown=user dukaan ./dukaan
COPY --chown=user scripts ./scripts
RUN uv sync --frozen --no-dev

# --- Space runtime config (override in Space Settings → Variables/Secrets as needed)
ENV DUKAAN_DATA_DIR=/data \
    DUKAAN_GRADIO_HOST=0.0.0.0 \
    DUKAAN_GRADIO_PORT=7860 \
    DUKAAN_LLM_CTX=8192 \
    HF_HOME=/home/user/.cache/huggingface
# HF_TOKEN (for gated Veena) is injected at runtime from the Space secret.

EXPOSE 7860
ENTRYPOINT ["bash", "scripts/space_entrypoint.sh"]
