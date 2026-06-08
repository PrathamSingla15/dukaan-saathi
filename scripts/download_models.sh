#!/usr/bin/env bash
# Download every model Dukaan Saathi needs, reproducibly, without touching the
# project's own venv (uses an ephemeral env with just huggingface_hub).
#
#   - Gemma-4-12B-it Q8_0 GGUF + vision mmproj  -> models/gemma4/  (llama.cpp)
#   - faster-whisper large-v3                   -> HF cache        (STT)
#   - facebook/mms-tts-hin                      -> HF cache        (TTS, default)
#
# NOTE: ai4bharat/indic-parler-tts (the most natural Hindi voice) is a GATED HF
# repo and is NOT downloaded here. To use it instead of MMS:
#   1. Request access at https://huggingface.co/ai4bharat/indic-parler-tts
#   2. `huggingface-cli login`  (paste a token with read access)
#   3. `uv pip install git+https://github.com/huggingface/parler-tts.git`
#   4. Run with  DUKAAN_TTS_ENGINE=parler  (tts.py falls back to mms if missing).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p models/gemma4

echo "[download_models] fetching models (this can take a while on first run) ..."

uv run --no-project --with huggingface_hub python - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download

# ---- Gemma-4-12B-it GGUF + vision projector (llama.cpp) ----
gemma_repo = "ggml-org/gemma-4-12B-it-GGUF"
gemma_dir = "models/gemma4"
for fname in ("gemma-4-12B-it-Q8_0.gguf", "mmproj-gemma-4-12B-it-Q8_0.gguf"):
    path = hf_hub_download(repo_id=gemma_repo, filename=fname, local_dir=gemma_dir)
    print(f"  [gemma]   {fname} -> {path}")

# ---- faster-whisper large-v3 (STT) -> default HF cache ----
wpath = snapshot_download(repo_id="Systran/faster-whisper-large-v3")
print(f"  [whisper] Systran/faster-whisper-large-v3 -> {wpath}")

# ---- facebook/mms-tts-hin (TTS, default engine) -> default HF cache ----
tpath = snapshot_download(repo_id="facebook/mms-tts-hin")
print(f"  [tts]     facebook/mms-tts-hin -> {tpath}")

print("[download_models] done.")
PY
