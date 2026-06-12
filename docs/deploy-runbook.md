# Deploy runbook — Modal (LLM) → HF Space (Gradio + STT + TTS on T4)

Two GPUs in two places:
- **Modal (L4 or T4)** runs the LLM (`llama-server` = Gemma + vision OCR) → set in code (`scripts/modal_llama.py`).
- **HF Space (T4)** runs Gradio + Whisper (STT) + Veena (TTS) → set in the Space **Settings UI**.

**Deploy Modal FIRST** — the Space needs the Modal URL as a secret.

Prereqs: a Modal account (your $250 credits) and HF membership in the `build-small-hackathon` org (you have it).

---

## PHASE 1 — host the LLM on Modal

**1. Install + authenticate (interactive — must be you):**
```bash
pip install modal
modal token new          # opens a browser, creates your token
```

**2. Deploy the LLM endpoint:**
```bash
cd /home/ayush_s/projects/pratham/dukaan-saathi
modal deploy scripts/modal_llama.py
```
What happens: Modal pulls the llama.cpp CUDA image, creates the `dukaan-hf-cache` Volume, downloads the Gemma Q4_K_M GGUF (~7 GB) into it, and starts **one warm L4** (`min_containers=1`). First deploy ~3–5 min.

It prints a stable URL like:
```
https://<your-workspace>--dukaan-llm-serve.modal.run
```
→ your **OpenAI base URL is that + `/v1`**. (Find it later with `modal app list` or the Modal dashboard.)

**3. Smoke-test it:**
```bash
curl https://<your-workspace>--dukaan-llm-serve.modal.run/health
# then a real call (first one ~30-60s while the model loads into VRAM):
curl https://<your-workspace>--dukaan-llm-serve.modal.run/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma-4-12b","messages":[{"role":"user","content":"namaste"}],"max_tokens":20}'
```

**Options:**
- **Cheaper T4 GPU:** `MODAL_GPU=T4 modal deploy scripts/modal_llama.py` (~$0.59/hr vs L4 $0.80; tighter VRAM for Gemma-12B — fine at `LLM_CTX=8192`).
- **Save credits between sessions:** `MIN_CONTAINERS=0 modal deploy scripts/modal_llama.py` (scale-to-zero; first call after idle cold-starts ~30s–2 min). Keep `min_containers=1` during judging.
- **Sponsor / ≤4B swap (MiniCPM-V):**
  ```bash
  LLM_REPO=openbmb/MiniCPM-V-4.6-gguf LLM_FILE=MiniCPM-V-4_6-Q4_K_M.gguf \
    LLM_MMPROJ=mmproj-model-f16.gguf modal deploy scripts/modal_llama.py
  ```

> The Gemma/MiniCPM GGUF repos are **public** — no HF token needed for the Modal side.

---

## PHASE 2 — host the Space on Hugging Face (T4)

**4. Accept the gated Veena license** (once): open <https://huggingface.co/maya-research/veena-tts> and accept the terms with the account whose token you'll use. *(Skip and TTS is silent — there's no MMS fallback anymore.)*

**5. Authenticate (interactive — must be you):**
```bash
pip install -U huggingface_hub
hf auth login            # paste a WRITE token
```

**6. Create the Space in the org:**
```bash
hf repo create build-small-hackathon/dukaan-saathi --repo-type space --space_sdk docker
```

**7. Push the code:**
```bash
cd /home/ayush_s/projects/pratham/dukaan-saathi
git add -A
git commit -m "Deploy: Docker GPU Space (Modal LLM + T4 STT/TTS)"
git remote add space https://huggingface.co/spaces/build-small-hackathon/dukaan-saathi
git push --force space HEAD:main
```
`.gitignore` already excludes `models/ vendor/ data/`, so the push is code-only (well under the 1 GB repo cap). `--force` lets our README (with the Space frontmatter) overwrite the auto-created one. *(If a push ever hits the size cap, use `hf upload build-small-hackathon/dukaan-saathi . --repo-type space` instead.)*

**8. Configure the Space (web UI → Settings) — these spend money, so they're yours to click:**
- **Hardware → Nvidia T4 small** ($0.40/hr) → Save. **This is how the T4 is "taken"** — HF runs your Docker container on a T4 machine; your `WHISPER_DEVICE=cuda` / `TTS_DEVICE=cuda` use it. Set **Sleep time → after 1 hour** to cap cost.
- **Variables and secrets → New secret:**
  - `DUKAAN_LLM_BASE_URL` = `https://<your-workspace>--dukaan-llm-serve.modal.run/v1`  (from Phase 1)
  - `HF_TOKEN` = your token (for gated Veena)
- **(optional) Storage → Small (20 GB, $5/mo)** so the SQLite DBs survive restarts.

**9. Watch the build** (Space → Logs):
- Docker build (~10–20 min first time: CUDA base + torch + deps).
- Then `space_entrypoint.sh` sees `DUKAAN_LLM_BASE_URL` is remote → **skips the local llama-server**, seeds the demo DB, launches Gradio. Whisper + Veena lazy-load on first use.

**10. Smoke-test the live Space (open the URL):**
- Type *"aaj ka hisaab dikhao"* → answer comes from the **Modal** LLM.
- Upload a challan photo → **Gemma vision OCR** (Modal) → parsed line items.
- Record a voice note → **Whisper** (T4) → **Gemma** (Modal) → tap 🔊 → **Veena** (T4) plays audio.
- Confirm the dashboard, festival nudge, and the haan/nahi write-confirm.

---

## Troubleshooting
- **First voice turn slow** — Whisper + Veena download (~10 GB) + load on first use; subsequent turns are fast. The app warms them at boot best-effort.
- **No audio** — check the gated-Veena license is accepted and `HF_TOKEN` is set on the Space (no MMS fallback now).
- **T4 OOM** (unlikely; ~11 GB used of 16 GB) — set `DUKAAN_WHISPER_MODEL=medium`, or `DUKAAN_VEENA_4BIT=true` (then add `bitsandbytes accelerate`).
- **LLM calls hang/slow** — if you scaled Modal to zero, the first call cold-starts (~30s–2 min). Keep `min_containers=1` during judging.
- **Dashboard shows "Gemma offline"** — the Space can't reach Modal; verify `DUKAAN_LLM_BASE_URL` (must end in `/v1`) and that the Modal app is deployed/running.
