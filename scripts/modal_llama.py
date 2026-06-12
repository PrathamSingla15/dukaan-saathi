"""Serve the Dukaan Saathi LLM (llama.cpp `llama-server`) on Modal — OpenAI-compatible,
vision-capable — for the Modal sponsor track.

    modal deploy scripts/modal_llama.py
    # → https://<workspace>--dukaan-llm-serve.modal.run
    # Point the Space at it:  DUKAAN_LLM_BASE_URL=https://<...>.modal.run/v1

Continuous hosting: `min_containers=1` keeps one L4 warm; Modal rolling-replaces a
container as it nears the 24h `timeout`, so it stays up with no manual restart.
$250 credits ≈ ~13 days at 24/7 on L4 ($0.80/hr). Set MIN_CONTAINERS=0 to
scale-to-zero between judging sessions (cold start ~30s–2min from the Volume).

Model is env-driven so the SAME script serves either flagship:
  • Gemma-4-12B (default, best quality)
  • MiniCPM-V 4.6  → OpenBMB + Tiny Titan:
        LLM_REPO=openbmb/MiniCPM-V-4.6-gguf
        LLM_FILE=MiniCPM-V-4_6-Q4_K_M.gguf   LLM_MMPROJ=mmproj-model-f16.gguf
    (MiniCPM-V mmproj needs a recent llama.cpp — the :server-cuda image is fine)
"""
import os
import subprocess

import modal

# --- model (override via env at deploy time) -------------------------------
MODEL_REPO = os.environ.get("LLM_REPO", "ggml-org/gemma-4-12B-it-GGUF")
MODEL_FILE = os.environ.get("LLM_FILE", "gemma-4-12B-it-Q4_K_M.gguf")
MMPROJ_FILE = os.environ.get("LLM_MMPROJ", "mmproj-gemma-4-12B-it-Q8_0.gguf")
LLM_CTX = os.environ.get("LLM_CTX", "8192")

# --- runtime knobs ----------------------------------------------------------
GPU = os.environ.get("MODAL_GPU", "L4")            # cheapest 24GB ($0.80/hr)
MIN_CONTAINERS = int(os.environ.get("MIN_CONTAINERS", "1"))  # 1 = always warm; 0 = scale-to-zero
PORT = 8080
MINUTES = 60
CACHE = "/cache"

app = modal.App("dukaan-llm")

# Prebuilt llama.cpp CUDA server image (ships the `llama-server` binary + CUDA 12.8
# runtime). `.entrypoint([])` clears its default entrypoint so Modal runs our server.
image = (
    modal.Image.from_registry("ghcr.io/ggml-org/llama.cpp:server-cuda", add_python="3.12")
    .pip_install("huggingface_hub")
    .entrypoint([])
)

# GGUF weights persist across cold starts in a Volume (downloaded once).
hf_cache = modal.Volume.from_name("dukaan-hf-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    volumes={CACHE: hf_cache},
    timeout=24 * MINUTES * MINUTES,    # 24h = the max; Modal rolling-replaces at the boundary
    min_containers=MIN_CONTAINERS,     # keep 1 warm → continuous serving
    scaledown_window=5 * MINUTES,      # idle grace before scaling extras down
)
@modal.concurrent(max_inputs=64)
@modal.web_server(port=PORT, startup_timeout=10 * MINUTES)
def serve():
    from huggingface_hub import hf_hub_download

    gguf = hf_hub_download(MODEL_REPO, MODEL_FILE, local_dir=CACHE)
    mmproj = hf_hub_download(MODEL_REPO, MMPROJ_FILE, local_dir=CACHE)
    hf_cache.commit()  # persist downloads for fast cold starts

    # Vision via --mmproj; tool-calling via --jinja (the model's OWN template — do
    # NOT pass an external chat template, which breaks Gemma/MiniCPM tool-calls).
    cmd = (
        f"llama-server -m {gguf} --mmproj {mmproj} "
        f"--host 0.0.0.0 --port {PORT} -c {LLM_CTX} -ngl 99 --jinja"
    )
    print("+", cmd)
    subprocess.Popen(cmd, shell=True)
