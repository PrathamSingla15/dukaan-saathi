#!/usr/bin/env bash
# Slurm GPU comprehensive end-to-end: start llama-server, wait for /health, run
# the full real-model battery (scripts/e2e_full.py) against the PRE-COMPUTED data/
# DBs (copied to a temp dir by the driver), then exit (server killed on exit).
#   sbatch scripts/e2e_full.sbatch   ·   tail -f logs/e2e_full_<jobid>.log
#SBATCH --job-name=dukaan_e2e_full
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=6
#SBATCH --mem=48G
#SBATCH --time=00:40:00
#SBATCH --output=logs/e2e_full_%j.log
set -uo pipefail
# Prefer DUKAAN_ROOT (a custom var Slurm won't clobber, unlike SLURM_SUBMIT_DIR
# which Slurm forces to the submission dir). Falls back to submit-dir / script path.
ROOT="${DUKAAN_ROOT:-${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
cd "$ROOT"
mkdir -p logs

# Pick a free port — the shared node often has :8080 held by another service.
if [ -z "${PORT:-}" ]; then
  for p in 8199 8211 8233 8257 8271 8293; do
    ss -ltn 2>/dev/null | grep -q ":$p " || { PORT=$p; break; }
  done
  PORT="${PORT:-8199}"
fi
# The Python client (config.LLM_BASE_URL) MUST point at the same port as the server.
export DUKAAN_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
echo "[e2e-full] job ${SLURM_JOB_ID:-?} on $(hostname) — PORT=$PORT — starting llama-server (log: logs/llama_e2e_full.log)"
PORT="$PORT" bash scripts/serve_llm.sh > logs/llama_e2e_full.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

echo "[e2e-full] waiting for llama-server /health ..."
for i in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "[e2e-full] llama-server ready (after ${i} checks)."
    break
  fi
  if ! kill -0 "$SRV" 2>/dev/null; then
    echo "ERROR: llama-server exited early — see logs/llama_e2e_full.log" >&2
    tail -n 40 logs/llama_e2e_full.log >&2 || true
    exit 1
  fi
  sleep 3
done

echo "[e2e-full] running scripts/e2e_full.py ..."
uv run python scripts/e2e_full.py
RC=$?
echo "[e2e-full] e2e_full.py finished rc=${RC}"
exit "${RC}"
