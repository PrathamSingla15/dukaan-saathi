#!/usr/bin/env bash
# Slurm GPU end-to-end: start llama-server, wait for /health, run the real-model
# e2e checks (scripts/e2e_check.py), then exit (server is killed on exit).
#   sbatch scripts/e2e_gpu.sbatch   ·   tail -f logs/e2e_<jobid>.log
#SBATCH --job-name=dukaan_e2e
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=6
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --output=logs/e2e_%j.log
set -uo pipefail
ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
mkdir -p logs

PORT="${PORT:-8080}"
echo "[e2e] job ${SLURM_JOB_ID:-?} on $(hostname) — starting llama-server (log: logs/llama_e2e.log)"
PORT="$PORT" bash scripts/serve_llm.sh > logs/llama_e2e.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

echo "[e2e] waiting for llama-server /health ..."
for i in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "[e2e] llama-server ready (after ${i} checks)."
    break
  fi
  if ! kill -0 "$SRV" 2>/dev/null; then
    echo "ERROR: llama-server exited early — see logs/llama_e2e.log" >&2
    tail -n 40 logs/llama_e2e.log >&2 || true
    exit 1
  fi
  sleep 3
done

echo "[e2e] running scripts/e2e_check.py ..."
uv run python scripts/e2e_check.py
RC=$?
echo "[e2e] e2e_check.py finished rc=${RC}"
exit "${RC}"
