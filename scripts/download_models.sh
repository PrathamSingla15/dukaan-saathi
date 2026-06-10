#!/usr/bin/env bash
# Download every model Dukaan Saathi needs, reproducibly, without touching the
# project's own venv (uses an ephemeral env with just huggingface_hub).
#
#   - Gemma-4-12B-it Q8_0 GGUF + vision mmproj  -> models/gemma4/  (llama.cpp)
#   - faster-whisper large-v3                   -> HF cache        (STT)
#   - maya-research/veena-tts + snac_24khz      -> HF cache        (TTS, DEFAULT)
#   - facebook/mms-tts-hin                      -> HF cache        (TTS fallback)
#
# NOTE: Veena (the default voice — speaks Hindi/English/Hinglish) is a GATED HF
# repo. `huggingface-cli login` with an authorized token (or set HF_TOKEN) BEFORE
# running this. Without access the veena download is skipped and the app falls
# back to MMS (Devanagari-only). ai4bharat/indic-parler-tts is another gated
# option: `uv pip install parler-tts` and run with DUKAAN_TTS_ENGINE=parler.
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

# ---- Veena (DEFAULT TTS) + its SNAC decoder -> default HF cache (GATED) ----
for repo in ("maya-research/veena-tts", "hubertsiuzdak/snac_24khz"):
    try:
        vpath = snapshot_download(repo_id=repo)
        print(f"  [veena]   {repo} -> {vpath}")
    except Exception as e:  # gated / not logged in — app will fall back to MMS
        print(f"  [veena]   SKIP {repo}: {type(e).__name__} — login with an authorized token to enable Veena.")

# ---- facebook/mms-tts-hin (TTS fallback) -> default HF cache ----
tpath = snapshot_download(repo_id="facebook/mms-tts-hin")
print(f"  [tts]     facebook/mms-tts-hin -> {tpath}")

print("[download_models] done.")
PY
