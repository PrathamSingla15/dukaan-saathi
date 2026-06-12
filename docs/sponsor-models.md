# Sponsor-model variants + Modal hosting

How to chase the sponsor tracks by swapping the main LLM (a config-only change — the app talks to any OpenAI-compatible endpoint via `DUKAAN_LLM_BASE_URL` / `DUKAAN_LLM_MODEL`), and how to host it continuously on Modal.

---

## The linchpin: MiniCPM-V 4.6 → wins OpenBMB **and** Tiny Titan in one swap

`openbmb/MiniCPM-V-4.6` — a **≤4B vision-language** model (Apache-2.0), multilingual (Hindi/Devanagari via its Qwen backbone), with an **official GGUF** repo and **llama.cpp** support. It's a near-drop-in for Gemma in our `llama-server` pipeline.

- **Model:** `openbmb/MiniCPM-V-4.6` · **GGUF:** `openbmb/MiniCPM-V-4.6-gguf` → **`MiniCPM-V-4_6-Q4_K_M.gguf`** + **`mmproj-model-f16.gguf`** (verified)
- **Unlocks:** 🏮 **OpenBMB** "Best MiniCPM Build" ($10k pool) **+** 🐜 **Tiny Titan** ($1,500, ≤4B) **+** strengthens 🏡 Backyard AI's "honest small-model fit" axis.
- **Swap (config only):**
  - Modal: set `LLM_REPO=openbmb/MiniCPM-V-4.6-gguf`, `LLM_FILE=MiniCPM-V-4_6-Q4_K_M.gguf`, `LLM_MMPROJ=mmproj-model-f16.gguf` in `scripts/modal_llama.py` and redeploy.
  - Or in-Space: point `DUKAAN_GEMMA_GGUF` / `DUKAAN_GEMMA_MMPROJ` at the MiniCPM files and set `DUKAAN_LLM_MODEL=minicpm-v`.
- **⚠️ Verify at build:** (1) llama.cpp mmproj vision for MiniCPM-V is **version-sensitive** — use a recent build (the `ghcr.io/ggml-org/llama.cpp:server-cuda` image) and follow the [MiniCPM-V llama.cpp CookBook](https://github.com/OpenSQZ/MiniCPM-V-CookBook/blob/main/deployment/llama.cpp/minicpm-v4_6_llamacpp.md); (2) OCR quality on **real Hindi bills/khata** (A/B vs Gemma — see below).

### The ≤4B stack (Tiny Titan eligibility — every model ≤4B)
| Role | Model | Size | ≤4B? |
|---|---|---|---|
| LLM + vision | `openbmb/MiniCPM-V-4.6` | ~1.3–3.3B | ✅ |
| STT | `faster-whisper large-v3` | ~1.55B | ✅ |
| TTS | `maya-research/veena-tts` (+ `snac_24khz`) | ~3B | ✅ (Veena is ≤4B) |

---

## Sponsor-by-sponsor

| Track | Decision | Model / action |
|---|---|---|
| 🟢 **Modal** (host) | **DO** | Host `llama-server` on Modal (`scripts/modal_llama.py`). Note it in the README. Earns the track regardless of which model. |
| 🏮 **OpenBMB** | **DO** (via MiniCPM-V) | `openbmb/MiniCPM-V-4.6` GGUF — config swap. Also unlocks Tiny Titan. |
| 🐜 **Tiny Titan** | **DO** (via MiniCPM-V) | Same MiniCPM-V build; every model ≤4B (MiniCPM-V ~3B, Whisper 1.55B, Veena ~3B). |
| 🌀 **OpenAI / Codex** | **OPTIONAL** | Not a model swap — the track wants **Codex-attributed commits**. Route remaining dev (Dockerfile, Modal script, polish) through OpenAI Codex ($100 credit). Low priority; "holistic use" ranks higher than light use. |
| 🟩 **NVIDIA / Nemotron** | **SKIP** | No vision-capable Nemotron <32B; our app is vision-centric (bill/khata OCR). Not worth a text-only variant. |
| 🔵 **Cohere** | **SKIP** | No prize track (partner only). Aya Vision 8B (vision+Hindi) has **no GGUF** → vLLM work for zero direct prize. |

### Flagship model decision (test-gated)
MiniCPM-V (1.3–3.3B) may trail Gemma-12B on messy Hindi handwritten-khata OCR + multi-tool reasoning. So:
1. Stand up **both** MiniCPM-V 4.6 and Gemma-4-12B on Modal.
2. **A/B test** on 5–10 real bills/khata + a multi-tool turn (compare OCR accuracy + tool-call correctness).
3. If MiniCPM-V holds → make it the **single flagship** (one Space → Backyard AI + OpenBMB + Tiny Titan + Modal + Best Agent). If weaker → **two Spaces**: Gemma-12B flagship + a MiniCPM-V entry for OpenBMB + Tiny Titan.

---

## Modal hosting — continuous serving, the 24h cap, $250

**Deploy:** `modal deploy scripts/modal_llama.py` → prints a stable URL `https://<workspace>--dukaan-llm-serve.modal.run`. Point the Space at it: `DUKAAN_LLM_BASE_URL=https://<...>.modal.run/v1`.

**The 24h cap is handled for you.** Modal's max function `timeout` is **24h (86,400s)**. The script sets `timeout=86400` + `min_containers=1`, so Modal keeps one L4 warm and, as a container nears the 24h limit, **rolling-replaces** it (a fresh warm container takes over) — **no manual restart, no downtime**.

**Budget ($250 credits, default on-demand L4 ≈ $0.80/hr):**
- **24/7 ≈ 312 GPU-hours ≈ ~13 days** — covers submission + the judging window.
- **Stretch it:** set `MIN_CONTAINERS=0` (scale-to-zero) between judging sessions — pay only on use; cold start ~30s–2min to load the GGUF from the `modal.Volume`. The demo **video is the canonical eval**, so cold starts are acceptable.
- **Recommended:** keep `min_containers=1` during the active judging days; scale-to-zero otherwise. Watch the Modal dashboard for credit burn.

**Weights:** cached in a `modal.Volume` (`dukaan-hf-cache`) so restarts are fast. Public models (Gemma, MiniCPM-V) need no token; for a gated model add a Modal secret.

**Architecture recap:** the **HF Space** (Docker, T4) runs Gradio + Whisper (STT) + Veena (TTS) and calls **Modal** for the LLM. The single-container L4 build (Dockerfile + `space_entrypoint.sh`) is the **self-contained fallback** — the entrypoint auto-detects: if `DUKAAN_LLM_BASE_URL` is remote it skips the local llama-server; if local it starts one.
