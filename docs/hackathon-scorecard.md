# Dukaan Saathi — Build Small Hackathon Scorecard

**Living checklist to track points captured vs. reachable.** Tick boxes as artifacts ship.

- **Today:** 2026-06-11 · **Deadline:** 2026-06-15
- **Track:** 🏡 Backyard AI · **Sponsor target:** 🟢 Modal (LLM hosted on Modal)
- **Flagship architecture:** HF Space (Gradio + STT + TTS) → **Modal-hosted llama-server** (Gemma or MiniCPM-V). See `docs/sponsor-models.md`.
- **Sources:** [Field Guide](https://build-small-hackathon-field-guide.hf.space/) · [Submit](https://build-small-hackathon-field-guide.hf.space/submit) · [Org](https://huggingface.co/build-small-hackathon)
- Full strategy: `~/.claude/plans/hey-actually-this-is-hidden-penguin.md`

Legend: ✅ earned · 🟡 nearly there · ⏳ planned/reachable · ❌ not pursuing

---

## 0. REQUIRED TO SUBMIT — the gate (nothing scores without these)
- [ ] ⏳ Gradio app deployed **as a Space in the `build-small-hackathon` org** (Docker OK)
- [ ] ⏳ **Demo video** of the app working (canonical eval if a live run is GPU-limited)
- [ ] ⏳ **One social-media post**, linked from the Space README
- [ ] ⏳ **README metadata/tags + links** (portal auto-updates tags; commit back)
- [x] ✅ Every model **< 32B params** (per-model cap; combine freely)

## 1. General track + Community Choice
- [ ] 🏡 **Backyard AI** — 1st **$4,000** / 2nd $2,500 / 3rd $1,500 / 4th $1,000 — ✅ fit, **aim 1st** (real-owner demo nails "they actually used it")
- [ ] 🗳️ **Community Choice** — **$2,000** (awarded **per track**) — ⏳ via social campaign

## 2. Merit badges — "Stack 'em on your sash" (feed 🎖️ Bonus Quest Champion)
- [ ] 🔌 **Off the Grid** — no cloud AI APIs, runs locally — ⚠️ **traded away** on the Modal-hosted flagship (cloud GPU). Only earned if we *also* ship the self-contained L4 Space — not the priority.
- [x] 🦙 **Llama Champion** — runs via llama.cpp — ✅ (`llama-server`, even on Modal)
- [x] 🎨 **Off-Brand** — custom frontend beyond default Gradio — ✅ (Bahi-Khata HTML/CSS/JS)
- [ ] 📓 **Field Notes** — blog/report on the build — 🟡 report written → **publish blog**
- [ ] 📡 **Sharing is Caring** — shared agent trace on the Hub — ⏳ export + upload
- [ ] 🎯 **Well-Tuned** — fine-tuned model published on HF — ❌ stretch (no fine-tune yet)
→ Modal flagship banks **4** badges (Llama Champion, Off-Brand, Field Notes, Sharing-is-Caring) → still competitive for Bonus Quest Champion.

## 3. Special awards ($8k pool)
- [ ] 🤖 **Best Agent** — $1,000 — ✅ capability (deepagents, 10 tools, vision) → **showcase**
- [ ] 🎨 **Off-Brand Award** — $1,500 — ⏳ strong (Bahi-Khata UI)
- [ ] 🎬 **Best Demo** — $1,000 — ⏳ real-owner Hindi video + social + blog
- [ ] 🎖️ **Bonus Quest Champion** — $2,000 — ⏳ if we stack the most badges (4 in hand)
- [ ] 🐜 **Tiny Titan** — $1,500 — ⏳ **reachable via MiniCPM-V 4.6** (≤4B vision) + Whisper (1.55B) + Veena (~3B). See `docs/sponsor-models.md`
- [ ] 🃏 **Judges' Wildcard** — $1,000 — can't target

## 4. Sponsor tracks — the "Trophy Cabinet"
- [ ] 🟢 **Modal** — 1st **10,000** / 2nd 7,000 / 3rd 3,000 credits — ⏳ **PRIMARY** — host the llama-server on Modal (we have **$250 credits ≈ ~13 days 24/7 on L4**). `scripts/modal_llama.py`
- [ ] 🏮 **OpenBMB** (MiniCPM) — $10k ($2.5k/$1.5k/$1k per track) — ⏳ **reachable via `MiniCPM-V-4.6` GGUF** (config-only swap; **also unlocks Tiny Titan**)
- [ ] 🌀 **OpenAI** (Codex) — $5k/$3k/**$1k** — ⏳/opt — the track rewards *building with Codex* (Codex-attributed commits), not running a GPT model. Route remaining dev via Codex ($100 credit) for light attribution (low priority)
- [ ] 🟩 **NVIDIA** (Nemotron) — 2× RTX 5080 — ❌ **skip** (no vision-capable Nemotron <32B; app is vision-centric)
- [ ] 🔵 **Cohere** — ❌ **no prize track** (partner only — provides Cohere Transcribe + Tiny Aya; Aya Vision has no GGUF). Skip.

---

## Tally
- **Banked today: 0** — nothing submitted yet.
- **Latent (done, unlocks on ship):** Backyard AI fit + 3 badges (Llama Champion, Off-Brand, + Off-Grid if self-hosted) + Best Agent capability + <32B.
- **Realistic max stack (if MiniCPM-V holds quality):** Backyard AI ($4k) + Community Choice ($2k) + Best Agent ($1k) + Off-Brand ($1.5k) + Best Demo ($1k) + Bonus Quest Champion ($2k) + **OpenBMB** (up to $2.5k) + **Tiny Titan** ($1.5k) + **Modal** (up to $10k credits). Awards stack — one Space can win several.

---

## 4-day execution
**Day 1 (Jun 11–12) — live Space on Modal (the blocker)**
- [ ] Confirm `build-small-hackathon` org write access; reserve Space name
- [ ] `modal deploy scripts/modal_llama.py` (Gemma first, known-good); get the `*.modal.run/v1` URL
- [ ] Deploy HF Space (Docker, **T4**): Gradio + STT + TTS; secret `DUKAAN_LLM_BASE_URL` = Modal URL; `HF_TOKEN` (gated Veena); `DUKAAN_DATA_DIR=/data` (+ Small persistent)
- [ ] Seed demo DB; smoke-test live (voice turn, photo challan, dashboard)

**Day 2 (Jun 12–13) — real-owner demo + model A/B**
- [ ] Onboard real shopkeeper via FSM (profile → inventory → khata → verify → commit; demo→real)
- [ ] Film tight 2–3 min Hindi demo (voice udhaar, challan receive, "kiska kitna baaki hai?", reminder, briefing) + shop b-roll
- [ ] **A/B test MiniCPM-V 4.6 vs Gemma** on real bills/khata + a multi-tool turn → pick the flagship model
- [ ] Stand up the **MiniCPM-V variant** on Modal (OpenBMB + Tiny Titan)

**Day 3 (Jun 13–14) — badges + blog**
- [ ] Sharing-is-Caring: export deepagents trace → upload to Hub
- [ ] Field Notes: publish HF blog from `docs/final_report.md`; link from README + social
- [ ] Capture agent-reasoning + UI screenshots (Best Agent, Off-Brand)

**Day 4 (Jun 14–15) — submit + social campaign**
- [ ] /submit portal: enter org Space, select Backyard AI + Modal + OpenBMB + Tiny Titan + all badges; commit README tags + video/blog/social links + write-up
- [ ] Post social (X + LinkedIn): tag `@huggingface @gradio @modal`, hashtag, link Space + video
- [ ] **Submit before deadline**; confirm Modal endpoint is up + Space runs
- [ ] Stretch if ahead: Well-Tuned LoRA; Codex-attributed commits

---

## How to submit (Space · blog · social)
1. **Space** — deploy the Gradio app **inside the `build-small-hackathon` org** (Docker OK, Gradio interface). Make it public.
2. **README** — the /submit portal **auto-updates the tags**; commit the README back. Add demo-video link, social-post link, short idea write-up.
3. **Demo video** — host publicly (YouTube/HF); judges use it even if a live GPU run isn't possible.
4. **Social post** — one post showcasing the app; **link it from the Space README**.
5. **Blog (Field Notes)** — publish on the HF blog; link from README + social.
6. **Submit the form** — org Space name + pick tracks/badges (Backyard AI, Modal, OpenBMB, Tiny Titan, every qualifying badge) + social URL. Before **Jun 15**.

## Verification
- **Modal:** `<url>/health` 200 + a vision `/v1/chat/completions` + a tool-call; leave 24h to confirm rolling replacement; watch credit burn on the dashboard.
- **Space:** open public URL clean → voice turn + photo challan + dashboard refresh; reply audio plays; write goes through haan/nahi; `server_up` true (Modal `/health`).
- **Links:** open video/blog/social incognito; README links resolve; portal shows selected tracks/badges.
