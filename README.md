---
title: Dukaan Saathi
emoji: 🏪
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: Hindi voice + photo inventory & udhaar ledger for kiranas
---

# 🏪 Dukaan Saathi

> A Hindi-first, voice-driven **inventory + udhaar (credit) ledger** assistant for a small kirana shop owner.
> *Small enough to run cheaply, big enough to change a shopkeeper's day.*

Built for the **Build Small Hackathon · Backyard AI track**. See [`design.md`](design.md) for the full architecture.

> **Deployment:** runs as a Hugging Face **Gradio Space**; the LLM (llama.cpp `llama-server` — Gemma-4-12B or MiniCPM-V) is served on **Modal**, while STT (faster-whisper) + TTS (Veena) run in the Space. See [`docs/sponsor-models.md`](docs/sponsor-models.md).

The shopkeeper just **talks in Hindi** (or snaps a photo of a bill / label). Everything else — stock, sales, purchases, credit, expiry alerts, festival nudges, polite payment reminders — is handled automatically.

## Stack (all local, runs on one GPU)

| Layer | Choice |
|---|---|
| LLM + Vision/OCR | **Gemma-4-12B** (multimodal, Q8_0 GGUF) via **llama.cpp** (`llama-server`, OpenAI-compatible) |
| Agentic framework | **deepagents** (LangChain) driving the local model via `ChatOpenAI` |
| Speech → Text | **faster-whisper** `large-v3` (Hindi, numpy-in, no system ffmpeg) |
| Text → Speech | **Veena** (`maya-research/veena-tts`, gated) — speaks Hindi/English/**Hinglish** via a SNAC decoder (`hubertsiuzdak/snac_24khz`) |
| Database | **two SQLite databases** — `inventory.db` (catalog/stock) + `transactions.db` (sales/khata), unified read via `ATTACH` |
| Frontend | **Gradio** single-screen app (mic · photo · chat · today-dashboard) |

## Quickstart

```bash
# 1. Install deps (uv)
uv sync

# 2. Download models (Gemma GGUF + mmproj, Whisper, Veena + SNAC)
#    Veena is a gated HF repo — `huggingface-cli login` with an authorized token first.
bash scripts/download_models.sh

# 3. Build + seed the two demo databases (inventory.db + transactions.db)
uv run python -m dukaan.db --reset

# 4a. Run everything under Slurm (recommended on the cluster) — llama-server + Gradio
sbatch scripts/run.sbatch

# 4b. ...or run locally for quick debugging
bash scripts/serve_llm.sh &        # starts llama-server on :8080
uv run python -m dukaan.app        # starts Gradio on :7860
```

## What it does

- **Voice credit ledger** — *"Sharma ji ne 200 ka udhaar liya"* / *"kiska kitna baaki hai?"*
- **Inventory + expiry** — track stock, flag items nearing expiry
- **Festival-aware stock-up nudge** — restock reminders before demand spikes
- **"Why isn't X selling?" diagnostic** — multi-turn agentic reasoning over sales trends
- **Reminder drafter** — drafts a polite Hindi collection message for overdue udhaar
- **Margin & stock-value visibility** — selling price · purchase price · MRP per item

## Layout

```
dukaan/        # app package (config, db, ops, llm, agent, tools, stt, tts, normalize, proactive, app)
  seed_inventory.py  # research-grounded catalog (~190 SKUs, suppliers, restocks)
  seed_ledger.py     # research-grounded customers + ~120 days of sales & udhaar
scripts/       # serve_llm.sh, run.sbatch, run_local.sh, download_models.sh
tests/         # pytest suite (db guard, ops, tools, numwords, e2e)
data/          # inventory.db + transactions.db (built by `python -m dukaan.db --reset`)
models/        # downloaded GGUF + mmproj
vendor/        # llama.cpp (built with CUDA)
```

See `tasks/todo.md` for build status.
