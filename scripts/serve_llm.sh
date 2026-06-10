#!/usr/bin/env bash
# Launch the local Gemma-4-12B (vision-capable) llama.cpp server for Dukaan Saathi.
# Local / debug helper — no Slurm here (see scripts/run.sbatch for the GPU batch job).
#
# Env overrides: DUKAAN_LLM_PORT / PORT (8080), CTX (32768), NGL (99 = fully GPU-offloaded).
# PORT wins if set; otherwise DUKAAN_LLM_PORT — the same var the app's client uses,
# so one setting moves both the server and the UI/health-check together.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The llama.cpp binary bakes an ABSOLUTE RUNPATH at build time; if the tree was
# moved (e.g. into this repo), that path is stale and the loader can't find the
# shared libs (libllama-server-impl.so, libggml-*.so). Point it at the real build
# dir so the server launches regardless of where it was originally built.
export LD_LIBRARY_PATH="$ROOT/vendor/llama.cpp/build/bin:${LD_LIBRARY_PATH:-}"

BIN="vendor/llama.cpp/build/bin/llama-server"
GGUF="models/gemma4/gemma-4-12B-it-Q8_0.gguf"
MMPROJ="models/gemma4/mmproj-gemma-4-12B-it-Q8_0.gguf"

PORT="${PORT:-${DUKAAN_LLM_PORT:-8080}}"
CTX="${CTX:-32768}"
NGL="${NGL:-99}"

if [[ ! -x "$BIN" ]]; then
  echo "ERROR: llama-server not found at $ROOT/$BIN" >&2
  echo "Build it first, e.g.:" >&2
  echo "  cmake -B vendor/llama.cpp/build -S vendor/llama.cpp -DGGML_CUDA=ON" >&2
  echo "  cmake --build vendor/llama.cpp/build --target llama-server -j" >&2
  exit 1
fi
if [[ ! -f "$GGUF" ]]; then
  echo "ERROR: model GGUF not found at $ROOT/$GGUF" >&2
  echo "Download the models first:  bash scripts/download_models.sh" >&2
  exit 1
fi
if [[ ! -f "$MMPROJ" ]]; then
  echo "ERROR: vision mmproj not found at $ROOT/$MMPROJ" >&2
  echo "Download the models first:  bash scripts/download_models.sh" >&2
  exit 1
fi

# --jinja: use the model's chat template (required for Gemma-4 tool-calling).
CMD=(
  "$BIN"
  -m "$GGUF"
  --mmproj "$MMPROJ"
  --host 127.0.0.1
  --port "$PORT"
  -c "$CTX"
  -ngl "$NGL"
  --jinja
)

echo "+ ${CMD[*]}"
exec "${CMD[@]}"
