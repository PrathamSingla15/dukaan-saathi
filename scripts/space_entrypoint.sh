#!/usr/bin/env bash
# HF Space entrypoint. Works in two modes, auto-detected from DUKAAN_LLM_BASE_URL:
#   • Modal-primary (default for the flagship): the URL points at a remote Modal
#     endpoint → we run ONLY Gradio + Whisper(STT) + Veena/MMS(TTS), calling Modal
#     for the LLM. Needs just a T4.
#   • Self-contained: the URL is local (or unset) → we ALSO download the Gemma GGUF
#     and start a local llama-server sidecar. Needs an L4.
# app.py:main() tolerates the LLM still warming up (dashboard shows server_up=false).
set -uo pipefail
cd "$(dirname "$0")/.."

# --- decide whether the LLM is local (we host it) or remote (Modal hosts it)
LLM_URL="${DUKAAN_LLM_BASE_URL:-http://127.0.0.1:8080/v1}"
case "$LLM_URL" in
  *127.0.0.1*|*localhost*|*0.0.0.0*) LOCAL_LLM=1 ;;
  *) LOCAL_LLM=0 ;;
esac
echo "[entrypoint] LLM_URL=$LLM_URL  LOCAL_LLM=$LOCAL_LLM"

# --- data dir: prefer persistent /data; fall back to $HOME if not attached.
if ! mkdir -p "${DUKAAN_DATA_DIR:-/data}" 2>/dev/null; then
  export DUKAAN_DATA_DIR="${HOME}/data"; mkdir -p "$DUKAAN_DATA_DIR"
fi
echo "[entrypoint] DATA_DIR=$DUKAAN_DATA_DIR"

# --- CTranslate2 (faster-whisper) needs libcudnn/libcublas; point the loader at
#     torch's bundled CUDA libs.
TORCH_LIB="$(uv run python -c 'import os,torch;print(os.path.join(os.path.dirname(torch.__file__),"lib"))' 2>/dev/null || true)"
export LD_LIBRARY_PATH="${TORCH_LIB}:${LD_LIBRARY_PATH:-}"

# --- seed the demo catalog/ledger only on first boot (idempotent thereafter).
if [ ! -f "${DUKAAN_DATA_DIR}/inventory.db" ]; then
  echo "[entrypoint] seeding demo DB"; uv run python -m dukaan.db --reset || true
fi

# --- local llama-server ONLY when we're hosting the LLM in-container.
if [ "$LOCAL_LLM" = "1" ]; then
  MODELS_DIR="${HOME}/app/models/gemma4"; mkdir -p "$MODELS_DIR"
  export DUKAAN_GEMMA_GGUF="${MODELS_DIR}/gemma-4-12B-it-Q4_K_M.gguf"
  export DUKAAN_GEMMA_MMPROJ="${MODELS_DIR}/mmproj-gemma-4-12B-it-Q8_0.gguf"
  uv run python - <<'PY'
import os
from huggingface_hub import hf_hub_download
repo = "ggml-org/gemma-4-12B-it-GGUF"
d = os.path.join(os.environ["HOME"], "app", "models", "gemma4")
for f in ("gemma-4-12B-it-Q4_K_M.gguf", "mmproj-gemma-4-12B-it-Q8_0.gguf"):
    print("[models]", hf_hub_download(repo_id=repo, filename=f, local_dir=d))
PY
  LLAMA="$(command -v llama-server || echo /app/llama-server)"
  echo "[entrypoint] starting local llama-server: $LLAMA"
  "$LLAMA" -m "$DUKAAN_GEMMA_GGUF" --mmproj "$DUKAAN_GEMMA_MMPROJ" \
    --host 127.0.0.1 --port 8080 -c "${DUKAAN_LLM_CTX:-8192}" -ngl 99 --jinja &
else
  echo "[entrypoint] using remote LLM at $LLM_URL — not starting a local llama-server"
fi

# --- Gradio app (foreground; Whisper + Veena lazy-load on first use).
exec uv run python -m dukaan.app
