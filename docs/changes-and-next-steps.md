# Dukaan Saathi — Changes & Next Steps (hackathon submission sprint)

**Session date:** 2026-06-11 · **Deadline:** 2026-06-15 · **Status:** Day-1 deploy artifacts ready; deploy pending your HuggingFace login.

This sprint adds the **submission packaging** (HF Space deploy + planning docs). It changes **no backend or UI logic** — `dukaan/` Python is untouched except for additive deploy files. The app's behavior is identical; we're just shipping it.

---

## 1. What changed

### New files
| File | What it does | Why |
|---|---|---|
| `docs/hackathon-scorecard.md` | Living checklist: every badge / track / special award mapped to our status, + the 4-day plan, submission steps, and verification | Single source of truth to "keep a check" on points |
| `Dockerfile` | Single-container **L4 GPU Space**. Base = `ghcr.io/ggml-org/llama.cpp:server-cuda` (CUDA 12.8.1, gives the `llama-server` binary); layers Python deps via `uv`, runs as uid 1000, exposes `7860` | The required submission artifact is a Gradio Space; this packages the existing app for HF Spaces |
| `scripts/space_entrypoint.sh` | Runtime startup: download Gemma Q4_K_M GGUF + vision projector → start `llama-server` (`--mmproj` + `--jinja`) in background → seed demo DB on first boot → launch Gradio. Handles `/data` fallback + cuDNN `LD_LIBRARY_PATH` | `app.py:main()` does **not** start `llama-server`; the Space needs an entrypoint that does |
| `.dockerignore` | Excludes `models/ vendor/ data/ logs/ *.db .git` from the image | Avoids the 1 GB git cap + a multi-GB image; models download at runtime instead |

### Modified files
| File | Change |
|---|---|
| `README.md` | Prepended HF Space YAML frontmatter (`title`, `emoji`, `sdk: docker`, `app_port: 7860`, `short_description`). Existing README content unchanged below it |
| `tasks/todo.md` | Added the "🏁 Hackathon submission sprint (Jun 11–15)" section at the top (active checklist); existing history preserved |
| `~/.claude/plans/hey-actually-this-is-hidden-penguin.md` | The full approved strategy plan (outside the repo). Corrected GPU (L4) and Modal economics |

### NOT touched
- No changes to `dukaan/*.py` (agent, session, ops, db, stt, tts, etc.), tests, or assets. App logic + the 59 headless / 9 GPU e2e tests stand as-is.

---

## 2. Key decisions & corrections (from deployment research)
- **GPU = Nvidia L4** ($0.80/hr) — the *cheapest* 24 GB tier, with a 400 GB disk (vs A10G $1.00/hr, 110 GB). Earlier note said A10G; **L4 is better**.
- **Modal free credit is $30/mo, not $250** ($250 = paid Team plan). A week of always-warm L4 ≈ $134, so Modal runs **scale-to-zero** and is showcased in the demo video — it still earns the sponsor track, but the live Space stays self-hosted (also keeps the 🔌 Off-the-Grid badge clean).
- **ZeroGPU won't work** for our architecture (Gradio-SDK-only + per-call GPU release can't host a persistent `llama-server`) → a paid dedicated GPU Space is correct.
- **Models download at runtime** to the 400 GB ephemeral disk (never committed to git). The image stays small.
- **`/data` is ephemeral unless you add persistent storage** → add the **Small (20 GB, $5/mo)** tier so the real-owner SQLite DBs survive restarts. Models stay on ephemeral disk.
- **Gemma config:** Q4_K_M (~7 GB) on L4, `DUKAAN_LLM_CTX=8192`, Veena **bf16** (4-bit fallback if VRAM is tight). Filenames verified to exist in `ggml-org/gemma-4-12B-it-GGUF`.

---

## 3. Next steps

### Day 1 — deploy the Space (the only true blocker) ← we are here
**You run the interactive login** (must be you):
```bash
! hf auth login        # token that can access gated maya-research/veena-tts
```
**Then create + push** (I can run these once you're logged in, or you run them):
```bash
hf repo create build-small-hackathon/dukaan-saathi --repo-type space --space_sdk docker

cd /home/ayush_s/projects/pratham/dukaan-saathi
git add Dockerfile scripts/space_entrypoint.sh .dockerignore README.md docs/ tasks/
git commit -m "Add Docker GPU Space deploy (llama-server + Gradio)"
git remote add space https://huggingface.co/spaces/build-small-hackathon/dukaan-saathi
git push --force space HEAD:main
```
**Then in Space → Settings (web UI — these spend money, so you click them):**
- **Hardware → Nvidia L4** (set a sleep timer to cap cost)
- **Variables & secrets → secret `HF_TOKEN`** (first accept terms at `huggingface.co/maya-research/veena-tts`, else voice falls back to MMS)
- **Storage → Small (20 GB, $5/mo)** so the real-owner DBs persist

**First-build watchlist (I'll fix any that bite):**
1. **VRAM OOM** → `DUKAAN_WHISPER_MODEL=medium` or `DUKAAN_VEENA_4BIT=true` (+`bitsandbytes accelerate`)
2. **faster-whisper `libcudnn` error** → the entrypoint's `LD_LIBRARY_PATH` is the knob
3. **Org create fails (permission)** → confirm you're a writer on `build-small-hackathon`

**Smoke test the live Space:** open public URL → a voice turn, a photo-challan receive, a dashboard refresh; confirm reply audio + the haan/nahi write confirm.

### Days 2–4 — see `docs/hackathon-scorecard.md` for the full checklist
- **Day 2:** onboard a real kirana owner via the FSM → film a 2–3 min Hindi demo → upload.
- **Day 3:** deploy `llama-server` on Modal (flip `DUKAAN_LLM_BASE_URL`, scale-to-zero) for the Modal track · export + upload an agent trace (Sharing-is-Caring badge) · publish a HF blog from `docs/final_report.md` (Field Notes badge).
- **Day 4:** submit via the `/submit` portal (select Backyard AI + all badges, commit README tags, add video/blog/social links) · post on X + LinkedIn (`@huggingface @gradio @modal`) · submit before the deadline.

---

## 4. Two confirmations needed from you
1. Are you a **writer on the `build-small-hackathon` org**? (Gates the `create` command.)
2. Want me to **run the create + push** once you've done `!hf auth login`? (You set hardware/secret/storage in the UI.)

## Related docs
- `docs/hackathon-scorecard.md` — points checklist + full 4-day plan + submission how-to
- `docs/final_report.md` — as-built reference (basis for the Field Notes blog)
- `INTERFACE.md` — backend↔UI/Space contract
- `docs/sponsor-models.md` — sponsor-model variants (MiniCPM-V) + Modal hosting

---

## Revision 2 (2026-06-11) — Modal-primary + sponsor models
- **Hosting flipped to Modal-primary.** The HF Space (Docker, **T4**) runs Gradio + STT + TTS and calls a **Modal-hosted llama-server** for the LLM (earns the Modal track). `scripts/modal_llama.py` deploys it; `scripts/space_entrypoint.sh` now auto-skips the local llama-server when `DUKAAN_LLM_BASE_URL` is remote. The single-container L4 Dockerfile stays as the self-contained fallback.
- **$250 Modal credits** ≈ ~13 days 24/7 on L4. Continuous serving via `min_containers=1` + `timeout=86400` (Modal rolling-replaces at the 24h cap — no manual restart). Scale-to-zero between sessions to stretch credits.
- **Field-guide corrections:** Community Choice is per-track ($2k for Backyard AI); OpenAI 3rd = $1k; **Cohere has no prize track** (skip); model rule is per-model <32B.
- **Sponsor models:** **MiniCPM-V 4.6** (≤4B GGUF, config swap) → **OpenBMB + Tiny Titan** in one move. NVIDIA Nemotron + Cohere skipped. OpenAI = Codex-attributed commits (optional). Flagship model chosen by an A/B test (MiniCPM-V vs Gemma) on real bills/khata.
- **Trade-off:** Modal hosting forfeits the Off-the-Grid badge (cloud GPU) — worth it for the $10k Modal track.
- New/changed files: `scripts/modal_llama.py` (new), `scripts/space_entrypoint.sh` (remote-LLM guard), `docs/sponsor-models.md` (new), `docs/hackathon-scorecard.md` (corrected).

---

## Revision 3 (2026-06-12) — festival calendar fix + hardcode audit + Modal scope locked
- **Hardcoded-numbers audit:** the "no hardcoding" claim holds — business thresholds are all in `config.py`, dashboard figures come from SQL. The only date literal (`seed_ledger.py:458`) lived in the standalone `__main__` stub; the real seed anchors to `dt.date.today()` (`db.py:305`). Fixed the stub to `today()` anyway.
- **Festival calendar reworked** (the real gap): `dukaan/data/festival_overrides.json` is now a **verified comprehensive Indian festival calendar, 2026–2030** (~26 festivals; dates checked against drikpanchang — even caught a python-holidays Ram Navami 2029 bug). `proactive._load_festivals` now **surfaces every festival** (no more dropping un-hinted holidays), with `holidays.India` as the fallback for years beyond the dataset. Muslim festivals flagged `estimated` (moon-sighting ±1 day). **Verified: 59 pass / 2 skip.**
- **Modal scope locked = LLM(+OCR) on Modal; STT (Whisper) + TTS (Veena) stay in a small T4 Space.** Rationale: the cash prizes don't care where models run; the Modal *track* is won by hosting the LLM; keeping STT/TTS in-process keeps the voice loop snappy with near-zero refactor. Moving STT/TTS to Modal (free CPU Space) is a clean future add.
- Changed: `dukaan/data/festival_overrides.json`, `dukaan/proactive.py`, `tests/test_festival_intent.py`, `dukaan/seed_ledger.py`.
