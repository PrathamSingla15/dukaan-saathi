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

## Stack (open-weight models, ≤32B)

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

## Hosting & deployment (Modal + HF Space)

The live submission is a **Hugging Face Docker Space** that offloads all GPU work to **Modal**:

- **Modal** (`scripts/modal_app.py` · app `dukaan-llm` · one **L4**, kept warm with `min_containers=1`) serves *everything on one GPU* — the **LLM + vision/OCR** (`llama-server`, Gemma-4-12B GGUF, OpenAI-compatible `/v1`), **STT** (`/stt`, faster-whisper) and **TTS** (`/tts`, Veena). Deploy: `PYTHONPATH="$PWD" modal deploy scripts/modal_app.py`.
- The **HF Space** runs the Gradio "Bahi-Khata" UI on free CPU and calls Modal via the secrets `DUKAAN_LLM_BASE_URL` / `DUKAAN_STT_BASE_URL` / `DUKAAN_TTS_BASE_URL` (+ `HF_TOKEN` for gated Veena). One warm GPU = one bill, and the whole stack is **open-weight models (≤32B)** — no proprietary AI APIs.
- Warm the endpoint ~2 min before a demo: `uv run python scripts/prewarm.py https://<workspace>--dukaan-llm-serve.modal.run`.

A self-contained single-container **L4 Space** (`Dockerfile` + `scripts/space_entrypoint.sh`) is kept as a fallback that runs the LLM in-process; the entrypoint auto-detects local vs remote from `DUKAAN_LLM_BASE_URL`.

## Tracks & badges

| Target | Evidence |
|---|---|
| 🏡 **Backyard AI** (Track-1) | A real kirana owner's daily problem — voice udhaar, challan OCR, FEFO/expiry, festival nudges — run on the owner's real books (demo video). |
| 🟢 **Modal** (sponsor) | LLM + OCR + STT + TTS all hosted on Modal (`scripts/modal_app.py`). |
| 🤖 **Best Agent** | deepagents loop · 10 tools (read + write + vision OCR) · confirm-before-write · a visible tool-call trace under every reply. |
| 🎨 **Off-Brand** | Custom "Bahi-Khata" HTML/CSS/JS ledger UI (cream paper, brass numerals, instant EN⇄हिं). |
| 🦙 **Llama Champion** | Runs on `llama.cpp` (`llama-server`). |
| 📓 **Field Notes** | Build write-up / blog (linked below). |
| 📡 **Sharing-is-Caring** | Exported agent trace (`scripts/export_trace.py`). |
| 🐜 **Tiny Titan** / 🏮 **OpenBMB** | Companion entry swaps the LLM to **MiniCPM-V 4.6 (≤4B)** — config-only (`DUKAAN_LLM_*`). |

## Evaluation

- **Headless suite** — `uv run pytest -q` → **56 pass / 2 skip** (LLM, STT and vision mocked): cross-DB integrity, FEFO lots, balances, staging/oversell, tools, onboarding, festival intent.
- **Real-model end-to-end** — `scripts/e2e_full.py` drives ~24 real Gemma turns over the seeded kirana data across **38 hard checks**: tool routing (read vs write), ground-truth lookups, confirm-before-write, FEFO sale, qty-merge restock, oversell hard-block, SQL guard, ambiguity clarification, vision challan receive, Hindi STT, TTS, morning briefing, festival calendar. GPU smoke: `scripts/e2e_gpu.sbatch`.

## Demo · blog · social

- 🎬 Demo video: _add link_
- 📓 Blog (Field Notes): _add link_
- 📣 Social post: _add link_

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
