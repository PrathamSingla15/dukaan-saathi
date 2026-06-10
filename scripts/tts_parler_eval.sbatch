#!/usr/bin/env bash
# Generate indic-parler-tts samples on a GPU (Slurm). Uses the isolated
# parler-eval venv so the project's transformers 5.x stays untouched.
#SBATCH --job-name=parler-eval
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:20:00
#SBATCH --output=/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/logs/parler_eval_%j.out
set -euo pipefail

PY=/home/shivank_g/parler-eval/bin/python
SCRIPT=/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/scripts/tts_parler_eval.py

echo "[sbatch] host=$(hostname) job=$SLURM_JOB_ID gpu=$CUDA_VISIBLE_DEVICES"
nvidia-smi --query-gpu=index,name --format=csv,noheader || true
"$PY" "$SCRIPT"
echo "[sbatch] done."
