#!/usr/bin/env bash
# Run the whole Dukaan Saathi demo on the LOCAL node (login-node debug — no Slurm).
# Starts llama-server in the background, waits for it to come up, then launches
# the Gradio app. Ctrl-C (or any exit) kills the server too.
#
# For a proper GPU allocation on the cluster use:  sbatch scripts/run.sbatch
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p logs

PORT="${PORT:-8080}"

echo "[run_local] starting llama-server on 127.0.0.1:${PORT} (log: logs/llama_server.log)"
PORT="$PORT" bash scripts/serve_llm.sh > logs/llama_server.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

echo "[run_local] waiting for llama-server /health ..."
for i in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "[run_local] llama-server is ready (after ${i} checks)."
    break
  fi
  if ! kill -0 "$SRV" 2>/dev/null; then
    echo "ERROR: llama-server exited early — see logs/llama_server.log" >&2
    tail -n 30 logs/llama_server.log >&2 || true
    exit 1
  fi
  sleep 3
done

if ! curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
  echo "ERROR: llama-server did not become ready in time — see logs/llama_server.log" >&2
  exit 1
fi

echo "[run_local] launching Gradio app (python -m dukaan.app) ..."
exec uv run python -m dukaan.app
