# 🏁 Hackathon submission sprint (Jun 11–15) 🔄 ACTIVE

Single source of truth: `docs/hackathon-scorecard.md`. Backend + UI are DONE; this sprint ships the **required artifacts** (Space, video, social) and chases badges + the Modal track.

**Day 1 — live Space on Modal (blocker):**
- [ ] Confirm `build-small-hackathon` org access; reserve Space name
- [ ] `modal deploy scripts/modal_llama.py` (Gemma first) → get `*.modal.run/v1` URL
- [ ] HF Space (Docker, **T4**): Gradio+STT+TTS; secret DUKAAN_LLM_BASE_URL=Modal URL; HF_TOKEN; DUKAAN_DATA_DIR=/data (+Small persistent)
- [ ] Seed + smoke-test live

**Day 2 — demo + model A/B:** onboard real shopkeeper → film 2–3 min Hindi demo → upload · A/B MiniCPM-V 4.6 vs Gemma → pick flagship · stand up MiniCPM-V variant (OpenBMB + Tiny Titan)
**Day 3 — badges + blog:** share agent trace (Sharing-is-Caring) · publish HF blog (Field Notes) · screenshots (Best Agent, Off-Brand)
**Day 4 — submit:** /submit (Backyard AI + Modal + OpenBMB + Tiny Titan + badges) · social post (@huggingface @gradio @modal) · submit before deadline

---

# Dukaan Saathi — Track‑1 revision implementation (backend) 🔄 IN PROGRESS

Plan: /home/shivank_g/.claude/plans/hey-now-create-the-linked-otter.md
Scope: backend/architecture only — no Gradio UI, no Dockerfile/HF Space (teammate builds those).
Locked: full batch/FEFO lots · replies Hindi/Hinglish for now · STT large‑v3 + Hindi 2nd pass · backend + headless tests.

## Phase 0 — Foundation helpers + schema/migration ✅ DONE (verified)
- [x] db.py: `inventory_lots` + `app_meta` DDL; `backfill_lots()`; `clear_business_data()`; `data_mode()/set_data_mode()`; wired into `init_db`
- [x] shelf_life.py (new): `estimate_expiry()`, `shelf_life_days_for()` from seed_inventory shelf_life_days
- [x] resolve.py (new): `normalize_item_name()`, `resolve_item()`, `candidates()` (rapidfuzz + difflib fallback)
- [x] i18n.py (new): never‑empty STT/OCR fallback strings (hi + bilingual); stub multilingual seam
- [x] config.py: STT_LANGUAGE='' (auto), STT_HINDI_MODEL/THRESHOLD, STT_MIN_CONFIDENCE/MAX_NOSPEECH, RESOLVE_*, CONFIRM_WRITES, FESTIVALS_OVERRIDES_PATH
- [x] deps: rapidfuzz 3.14.5, holidays 0.98 (uv)

## Phase 1 — ops.py (only writer): lots / FEFO / oversell / resolve ✅ DONE (verified)
- [x] `_add_lot()` create‑or‑merge (never clobber prior‑batch expiry)
- [x] `_consume_fefo()`, `_recompute_item_qty()` (sole writer of inventory.qty), `lots_for_item()`, `expiring_lots()`
- [x] rewrite `add_inventory`/`record_purchase` to create lots (optional `resolved_item_id`)
- [x] `record_sale`: oversell hard‑block before FEFO consume
- [x] `resolve_item/resolve_customer/candidates_*` wrap resolve.py; `expiring_soon`/`item_detail` → lots
- [x] VERIFIED: FEFO drains earliest first, qty==sum(lots) zero drift, oversell blocks, merge works, 29 data-layer tests green

## Phase 2 — capture: multilingual STT + structured OCR ✅ DONE
- [x] stt.py: `TranscribeResult`; auto‑detect; Hindi 2nd pass (vasista22 ct2‑int8); back‑compat shim
- [x] normalize.py: `OcrResult`/`DescribeResult`; never empty; `_CHALLAN_PROMPT`/`_KHATA_PROMPT`; `parse_challan()`/`parse_khata()` + json salvage
- [x] downloaded vasista22 ct2‑int8 model (cached + loadable)

## Phase 3 — staging + agent loop ✅ DONE
- [x] staging.py (new): `_PENDING`, `stage_op`, `commit_pending`, `clear_pending`, `bind_thread`/`current_thread`
- [x] tools.py: write tools stage (CONFIRM_WRITES); `confirm_pending_tool` (10 tools); resolve+disambiguate
- [x] agent.py: killed classify_intent LLM call → `_intent_from_tool_calls`; run_agent returns intent+pending; confirm rule
- [x] VERIFIED: full offline suite 29 passed + 2 e2e skipped; cross-module imports clean; stage→confirm works

## Phase 4 — wizards ✅ DONE
- [x] receiving.py (new): `stage_receive()` (parse+resolve, merge/new, editable), `commit_receive()` (→ ops.record_purchase). Smoke: merge+new, restock verified.
- [x] onboarding.py (new): FSM PROFILE→ROUGH_INVENTORY→KHATA→VERIFY→COMMIT; persisted in app_meta; verify‑back; replaces seed→real. Smoke: 2 items + balance 500 verified.

## Phase 5 — orchestration seam ✅ DONE
- [x] session.py (new): `TurnResult`, `PendingConfirmation`, `handle_turn()`, `confirm_pending()`, `dashboard_snapshot_struct()`, `morning_briefing()`. Mock smoke verified.
- [x] app.py: `respond()` → thin adapter onto `session.handle_turn`; dashboard/alerts kept; imports cleaned

## Phase 6 — proactive ✅ DONE
- [x] proactive.py: festival calendar via holidays + festival_overrides.json (15 hints, 2026/2027 extra); expiry_watcher → lots "(anumanit)". Smoke: Karwa Chauth/Diwali found.

## Phase 7 — tests + handoff ✅ DONE
- [x] headless pytest: test_lots_fefo (6), test_resolve (4), test_receiving (4), test_onboarding (4), test_session (5), test_festival_intent (6), test_session_confirm (1)
- [x] existing tests green (oversell → hard-block; record_sale_tool → stage-then-confirm)
- [x] INTERFACE.md (17 KB) for the teammate (session surface, contracts, env knobs, model download)

## GPU e2e (real-model proof) ✅ DONE — 9/9 (Slurm job 1327, rc=0)
- [x] scripts/e2e_gpu.sbatch + e2e_check.py: real Gemma agent lookup + write-via-confirm; vision challan→3 lines→auto-receive; TTS→STT round-trip (hi, conf 1.00)
- [x] Fixed 2 real-model bugs the headless mocks missed: (1) staging thread ContextVar → process global (deepagents hides ctxvar from tools); (2) prompt made the model pre-ask instead of calling the write tool → now calls it immediately so it stages.
- [x] Hardened: session.handle_turn resolves a staged write's yes/no deterministically (commit turn no longer depends on the model).

## Review — DONE ✅
**Shipped (backend only; teammate builds UI + HF Space on top via INTERFACE.md):**
- FEFO batch/expiry: `inventory_lots` under the merged item row; `inventory.qty`=cached SUM(lots); restock add-or-merge a lot; sale drains earliest-expiry; expiry estimated from shelf-life when challans omit it.
- Robust quantity-merge: `resolve.py` so variant names merge (no duplicate rows); ambiguity → "kaun sa?".
- Multilingual STT: large-v3 auto-detect + Hindi/rural 2nd pass (vasista22); structured result + never-empty fallback.
- Structured OCR + `parse_challan`/`parse_khata` + never-empty "re-upload" fallback.
- Challan auto-receive wizard (`receiving.py`); real-owner onboarding FSM (`onboarding.py`, demo→real).
- Confirm-before-write (`staging.py`+`confirm_pending_tool`) + oversell hard-block + killed the extra per-turn LLM call.
- Festival calendar via `holidays.India` + `festival_overrides.json` (2026/2027+, not hardcoded).
- UI-agnostic seam `session.py`; `app.py` thin adapter; `INTERFACE.md` handoff.

**Verification:** 59 headless tests pass (mock LLM/STT/vision) + 9/9 real-model GPU e2e (Slurm 1327).
**Scope honored:** NO Gradio screens / Dockerfile / HF Space built. Replies Hindi/Hinglish for now (multilingual = documented future switch).
**Deferred:** conversational challan-in-chat tool (wizard is the deliverable); one-call briefing optimization; multilingual reply+TTS.

---

# UI Rewrite — "Bahi-Khata" interface ✅ DONE (2026-06-09)

**Goal:** Rewrite the live Gradio app (`dukaan/app.py`) into a beautiful custom-HTML
"Bahi-Khata ledger" interface. English default + instant Hindi toggle. Surface most
features. UI-only — NO backend logic changes. Deploys straight to the HF Space.

**Decisions locked:** rewrite live app (not a throwaway prototype) · Bahi-Khata aesthetic
(cream paper, red margin rules, indigo ink, brass numerals, humanist serifs) · Gradio 6.16
(css/js/head/theme on `launch()`; `gr.HTML` supports `.click()`) · agent replies stay Hindi
(backend constraint), ALL chrome toggles EN⇄HI client-side.

## Build
- [ ] `assets/style.css` — full aesthetic (paper grain, ledger rules, brass numerals, motion, all components, `.i18n-en/.i18n-hi` visibility, responsive)
- [ ] `assets/head.html` — Google Fonts (Fraunces / Tiro Devanagari Hindi / IBM Plex Mono) + global JS (lang toggle, page nav, click delegation, ask-prefill, init)
- [ ] `app.py` rewrite — render helpers + 6 pages + masthead/nav/toggle + import-light `main()`

## Pages (surface most features)
- [ ] Aaj/Today — morning briefing + dashboard cards (stock value, today's sales, expiring/FEFO, low stock, udhaar, slow movers, festival); offline-safe
- [ ] Baat-cheet/Talk — voice + photo + text → chat (ledger entries) + haan/nahi confirm bar + TTS + intent/lang chips + STT/OCR retry states
- [ ] Khata/Credit — udhaar customers (overdue flags) + WhatsApp reminder drafts
- [ ] Maal/Stock — stock value + expiring lots + low stock + slow movers tables
- [ ] Saamaan/Receive — challan photo → editable preview → commit
- [ ] Naya Khata/Setup — onboarding wizard (profile→stock→khata→verify→commit), gated on `is_onboarding_active()`

## Verify
- [ ] `build_ui()` constructs, no model load at import
- [ ] Every HTML helper renders against a real `dashboard_snapshot_struct()`
- [ ] Existing pytest suite still green (heavy deps stubbed)
- [ ] Launch headless + screenshot Today / Talk + EN⇄HI flip

## Review ✅ DONE (verified, 2026-06-09)
**Delivered (UI-only, zero backend changes):**
- `dukaan/app.py` rewritten — custom-HTML ledger interface; 6 pages (Today / Talk / Khata / Stock /
  Receive / Setup); render helpers for every surface; import-light; imports only the seam
  (`session`/`onboarding`/`receiving`) + `proactive`/`config`/`db`.
- `dukaan/assets/style.css` — full Bahi-Khata system (paper grain, red margin rule, brass numerals,
  motion, all components, `.i18n-en/.i18n-hi` toggle, responsive).
- `dukaan/assets/head.html` — Google Fonts (Fraunces / Tiro Devanagari Hindi / IBM Plex Mono) + client
  JS (instant EN⇄Hindi toggle, page nav, click delegation, tap-to-ask, init).
- `main()` now injects CSS via `head=build_head()` (NOT `css_paths`, which Gradio scopes under
  `.contain` and would break the `body.lang-hi` toggle — see lessons.md).

**Verified:**
- `build_ui()` constructs with no model load at import; every render helper renders against a live
  `dashboard_snapshot_struct()`.
- pytest: 55 passed / 2 skipped (no regressions).
- Launched headless + screenshotted all 6 pages + the EN⇄HI flip (`logs/ui_*.png`). DOM-probe confirmed
  Hindi spans flip to `display:inline` with real width and the Devanagari/Tiro font.

**Notes / follow-ups (not blocking):**
- Agent replies stay Hindi (backend constraint), surfaced intentionally; all chrome toggles.
- Receive preview is read-only (parse → review → commit); inline row-edit deferred (re-upload to redo).
- For the HF Space: keep the Google Fonts `<link>` (Spaces have internet); Noto is a CSS fallback.
- Screenshots in `logs/ui_*.png` are verification artifacts — safe to delete.
