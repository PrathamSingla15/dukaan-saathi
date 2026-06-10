#!/usr/bin/env bash
# Generate indic-parler-tts AND maya-research/veena-tts samples on a GPU (Slurm).
# Uses the isolated parler-eval venv (transformers 4.46.1 + parler-tts + snac +
# bitsandbytes) so the app's transformers 5.x stays untouched.
#SBATCH --job-name=tts-eval
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/logs/tts_eval_%j.out
set -uo pipefail

PY=/home/shivank_g/parler-eval/bin/python
DIR=/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/scripts
export HF_HUB_OFFLINE=0

echo "[sbatch] host=$(hostname) job=${SLURM_JOB_ID:-NA} gpu=${CUDA_VISIBLE_DEVICES:-NA}"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

echo ""; echo "########## PARLER ##########"
"$PY" "$DIR/tts_parler_eval.py"; echo "[parler] exit=$?"

echo ""; echo "########## VEENA ##########"
"$PY" "$DIR/tts_veena_eval.py"; echo "[veena] exit=$?"

echo "[sbatch] done."
