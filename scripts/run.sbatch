#!/usr/bin/env bash
# Slurm batch job: run the entire Dukaan Saathi demo inside ONE GPU allocation.
# Starts llama-server (GPU), waits for /health, then runs the Gradio app on
# 0.0.0.0:7860. The llama-server is killed automatically when the job ends.
#
# Submit with:   sbatch scripts/run.sbatch
# Watch logs:    tail -f logs/dukaan_<jobid>.log
#SBATCH --job-name=dukaan
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=logs/dukaan_%j.log
set -euo pipefail
# In a Slurm batch job, ${BASH_SOURCE[0]} is Slurm's spool copy (unwritable), so
# prefer $SLURM_SUBMIT_DIR (the dir sbatch was invoked from = project root).
ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
mkdir -p logs

PORT="${PORT:-8080}"

echo "[run.sbatch] job ${SLURM_JOB_ID:-?} on $(hostname) — starting llama-server (log: logs/llama_server.log)"
PORT="$PORT" bash scripts/serve_llm.sh > logs/llama_server.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

echo "[run.sbatch] waiting for llama-server /health (timeout ~300s) ..."
for i in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "[run.sbatch] llama-server is ready (after ${i} checks)."
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

echo "[run.sbatch] launching Gradio app (python -m dukaan.app) on 0.0.0.0:7860 ..."
exec uv run python -m dukaan.app
