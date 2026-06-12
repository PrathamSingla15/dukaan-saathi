# Lessons — Dukaan Saathi

Non-obvious gotchas hit while building, with the fix, so they don't recur.

## Models / llama.cpp
- **Gemma-4 has a native "thinking" mode (`thinking=1` in its chat template).** With it on,
  `chat.completions` returns an EMPTY `content` (everything goes to `reasoning_content`) and is
  slow. Fix: send `extra_body={"chat_template_kwargs":{"enable_thinking":False}}` on every request
  (wired centrally in `llm.py` / `config.LLM_ENABLE_THINKING`). 0.2s vs 3.4s and non-empty.
- **No prebuilt CUDA llama-server for Linux** — build from source (`-DGGML_CUDA=ON`). Built without
  libcurl, so models are pre-downloaded with explicit `-m`/`--mmproj` paths.
- Gemma-4 tool-calling works via llama-server `--jinja` (verified through LangChain `bind_tools`).

## deepagents (0.6.8)
- Pass a `ChatOpenAI` **instance** to `create_deep_agent(model=...)`, never an `"openai:..."` string
  (string → OpenAI Responses API, which llama.cpp doesn't implement).
- Param is `system_prompt` (not `instructions`); final text at `result["messages"][-1].content`;
  raise `recursion_limit` via `config`; avoid subagents (open recursion bug #1698).
- The model reasons across tool calls, so disabling Gemma's internal thinking is fine.

## LangChain tools
- `@tool(parse_docstring=True)` is strict: needs a **blank line** between the summary and `Args:`,
  else "invalid Google-Style docstring" at import.

## Speech
- **MMS-TTS-hin only voices Devanagari** — romanized Hindi tokenizes to ~3 tokens (0.4s garbage).
  So (a) force the agent to reply in Devanagari, and (b) convert digits/₹ to Hindi number words
  (`numwords.py`) before TTS, since MMS can't pronounce Latin digits. `num2words` has no Hindi.
- faster-whisper needs no system ffmpeg (PyAV bundles it) and takes a numpy 16k float32 array.
- **Veena TTS on Modal: keep 4-bit (`DUKAAN_VEENA_4BIT=true`) — it's a VRAM fit, not an optimization.**
  bf16 Veena (~6GB) OOMs during *synthesis* alongside Gemma Q4 (`-ngl 99`) + Whisper large-v3 + the
  Hindi 2nd-pass Whisper + a 16k KV cache on the L4 (24GB), and the OOM degrades **silently** to empty
  audio (a 46-byte WAV, ~1s, HTTP 200). A fast `200 OK` is NOT proof TTS works — verify the WAV has real
  samples (`duration>1s`, `rms>0.01`), never just the status/timing. To speed TTS up, stream it
  sentence-by-sentence in the app; don't widen Veena's dtype.
- **Modal STT/TTS cold-start:** the models lazy-load on the first `/stt`,`/tts` call (≈27s/75s). Pre-load
  them in a daemon thread at container startup (`scripts/modal_app.py` `_warm_speech`) + `min_containers=1`
  so a warm container has all three models resident; warm STT ≈1.2s. Pre-warm before a demo with
  `scripts/prewarm.py <modal-url>`.

## Environment (vitallab2)
- The `hf` / `huggingface-cli` at `~/.local/bin` is **broken** (`ModuleNotFoundError: huggingface_hub`).
  Use `uv run --no-project --with huggingface_hub python -c "..."` instead.
- `ai4bharat/indic-parler-tts` is a **gated** HF repo; the machine's token (`rishavk77`) isn't
  authorized → fell back to open `facebook/mms-tts-hin`.
- gradio is **6.16** here (not 5.x): `gr.Chatbot` dropped `type="messages"` (dicts are the only
  model now) and `theme` moved from `Blocks()` to `launch()`.

## Slurm
- In an `sbatch` script, `${BASH_SOURCE[0]}` is Slurm's spool copy (unwritable dir). Use
  `${SLURM_SUBMIT_DIR}` for the project root, not a BASH_SOURCE-derived path.
- `scancel --wait` isn't supported by this Slurm version; `scancel <jobid>` works.

## Two databases + data generation
- Split into `inventory.db` (suppliers/inventory/purchases) + `transactions.db` (customers/sales/ledger).
  Writes route to the owning file; reads go through ONE connection that `ATTACH`-es both read-only as
  `inv` / `txn`, so the agent's LLM SQL can still JOIN across them. The SELECT guard still blocks
  LLM-issued ATTACH/writes — only *our* read connection attaches.
- Denormalise `item_name` into `sales` so common "top seller" queries don't need a cross-DB join.
- **Generated history ends at `end_date - 1`**, leaving "today" empty → the headline "aaj ki bikri"
  showed ₹0. Add a small deterministic "today so far" tranche in the loader.

## Workflow / subagent discipline
- A build subagent with a loose "verify your work" instruction went down a rabbit hole — it ran the
  FULL pytest + repeatedly re-tested the db loader, **stalling the pipeline so the next phase never
  started**. Scope build-agent prompts tightly: "write the file, py_compile + ONE import smoke,
  then RETURN — do NOT run pytest or test sibling modules." When a pipeline stage stalls, `TaskStop`
  the workflow and run the blocked stage as a focused single agent.

## PDF generation (markdown -> WeasyPrint)
- **kroki.io returns HTTP 403 to urllib's default User-Agent.** Send a browser-like `User-Agent`
  header (any non-empty UA works) on the Mermaid->PNG request; then POST-text and GET-deflate-base64
  both succeed.
- **vitallab2 has NO Devanagari font** (`fc-list :lang=hi` == 0) -> Hindi prints as tofu in WeasyPrint.
  Fix: download Noto Sans Devanagari (OFL) and `@font-face` it. The working source is the `google/fonts`
  variable TTF `ofl/notosansdevanagari/NotoSansDevanagari[wdth,wght].ttf` (URL-encode the brackets);
  declare `font-weight:100 900` so one file covers regular+bold. Put it AFTER "DejaVu Sans" in the
  stack so Latin stays DejaVu and only Devanagari falls back. The `notofonts/.../raw/...` GitHub path
  404s to an HTML page that masquerades as a .ttf — validate magic bytes `00010000` before trusting.
- **No colour-emoji font** either: strip pictographs (U+1F300-1FAFF + U+FE0F) and map the emoji you
  use (e.g. the ✅ check) to a DejaVu glyph (✓) before rendering, else they print as boxes.

## Track-1 revision (lots / confirm-before-write / multilingual / agents)
- **deepagents/LangGraph HIDES a ContextVar bind from tools.** A `contextvars.ContextVar` set in
  `run_agent` right before `agent.invoke()` is NOT visible inside a tool's execution (LangGraph runs
  tool nodes in a *copied* context). Symptom: confirm-before-write staged the op under thread
  `"default"` instead of the turn's `thread_id`, so the pending batch "vanished". Fix: carry per-turn
  state (the staging thread) in a **plain module global** set before invoke (visible process-wide,
  incl. threadpool tool execution), or read `thread_id` from the tool's injected `RunnableConfig`.
  **Only a real-model GPU e2e caught this** — headless tests staged directly and missed it. Lesson:
  always run at least one live-agent e2e for tool-state plumbing; mocks can't prove context flow.
- **Confirm-before-write changes test + agent contracts.** Write *tools* now STAGE (return a Hindi
  "haan/nahi" prompt) instead of writing; commit happens in `confirm_pending_tool`/`staging.commit_pending`.
  Update tool tests to stage→confirm. For a live agent write-test, either set `DUKAAN_CONFIRM_WRITES=false`
  or send a follow-up "haan" turn. `ops.*` still write immediately (wizards/tests call them directly).
- **FEFO lots under a merged master.** Keep `inventory.qty` as a CACHED `SUM(open lots.qty_remaining)`
  and `inventory.expiry_date` as the earliest open-lot expiry; make ONE `_recompute_item_qty()` the
  sole writer of `inventory.qty` (prevents drift — assert `qty == SUM(lots)` in tests). Restock =
  add-or-merge a lot (merge only when `(item_id, expiry_date, is_estimated)` all match, else new lot
  — never overwrite a prior batch's expiry); sale = drain earliest-expiry first. Satisfies "one
  merged row per item" while giving per-batch expiry. Backfill one lot per existing item in `init_db`.
- **resolve price-twins.** Items that normalize to the same string after price-stripping (e.g.
  "Parle-G Rs5"/"Rs10") resolve `ambiguous`, not `matched`. So `ops.add_inventory`/`record_purchase`
  must fall back to exact-name `find_item` on a non-`matched` resolve — else an exact restock name
  creates a DUPLICATE row instead of merging.
- **Workflow-script validator is a literal substring scanner.** It rejects a script containing
  `Math.random` / `Date.now` / `new Date` even inside prompt TEXT (not just executable code). Don't
  mention those literals in agent prompts; say "RNG/clock" instead.
- **STT auto-detect: never pass `language=""`.** faster-whisper wants `None` for auto-detect. With
  `config.STT_LANGUAGE=""` (the multilingual default), pass `language or (config.STT_LANGUAGE or None)`.
  `info.language` / `info.language_probability` + per-segment `no_speech_prob` are the confidence
  signals for never-empty STT fallbacks (ask the user to repeat instead of feeding "" to the agent).
- **Festivals offline via `holidays`.** `holidays.India(years=[...], categories=("public","optional"))`
  gives shopping-festival dates for ANY year (not hardcoded). It MISSES Karwa Chauth (add via a small
  bundled `festival_overrides.json`) and has no stock hints (layer a keyword→hint map on top). Scan
  current + next year so Dec→Jan lookahead works.

## UI rewrite (Bahi-Khata interface) — Gradio 6.16 gotchas
- **CSS/JS/head/theme moved off `Blocks()` to `launch()`.** In Gradio 6, `gr.Blocks(css=…, theme=…)`
  no longer exists — pass `css`/`css_paths`/`js`/`head`/`head_paths`/`theme` to `.launch()`. `gr.Blocks`
  only takes `title`/`fill_height`/`fill_width`/`analytics_enabled`/`mode`/`delete_cache`.
- **`css_paths` is auto-scoped — it BREAKS `body`-level selectors.** Gradio prefixes every css_paths
  rule with `.gradio-container.gradio-container-6-16-0 .contain `. So `body.lang-hi .i18n-hi {…}`
  becomes `…contain body.lang-hi .i18n-hi` which can NEVER match (`body` is above `.contain`), while
  the scoped base `.i18n-hi{display:none}` gains specificity and wins → the language toggle silently
  failed. FIX: inject the stylesheet as a raw `<style>` via the `head` param (global, unscoped). See
  `app.build_head()`. Anything relying on `<body>`/`<html>`-level classes must go through `head`.
- **`gr.HTML` supports `.click()` (+ `.select/.change/.submit`) in Gradio 6**, plus a `js=` param. So
  every button can be a custom bilingual HTML element wired to a backend fn — no `gr.Button` needed.
  Each clickable HTML must be its OWN component (the click fires on the component root).
- **Client-side nav/toggle beats server round-trips.** Page switching + EN⇄HI are pure JS (a delegated
  document listener on `[data-page]`/`[data-lang-btn]`, a body class flip, CSS `.dk-page--active`).
  Gradio components inside a CSS-hidden page still mount and fire — no `visible=` plumbing required.
- **f-strings: no backslashes in the expression part** (SyntaxError on 3.11 AND 3.12). Don't write
  `f'{"<span class=\"x\">" if c else ""}'`; precompute the fragment in a helper and interpolate it.
  Triple-quoted `f"""…{T("a","b")}…"""` is fine (delimiter is `"""`, not `"`).
- **Headless Chromium has NO Devanagari/emoji fonts** — Hindi/emoji render blank unless you drop Noto
  Sans Devanagari + Noto Color Emoji into `~/.fonts` + `fc-cache`. Real browsers/HF Spaces load them via
  the Google Fonts `<link>`; it's a test-env gap, but add Noto to the CSS stack as a fallback. Verify
  Devanagari with a DOM probe (`document.fonts.check`, computed `display`/width), not by eyeballing.
- **Verify real shapes, not the handoff doc.** `INTERFACE.md` claimed dashboard `festival` was a `str`;
  the code returns the full `festival_nudge()` dict, and `is_estimated`/`overdue` are int/bool. Always
  dump a live `session.dashboard_snapshot_struct()` before writing render code against it.

## UI round-2 fixes (TTS crash + dark mode + card lines), 2026-06-09
- **MMS-TTS-hin is Devanagari-ONLY (vocab 72 chars, 0 Latin, `is_uroman=False`).** Romanized/Hinglish
  replies tokenize to ZERO tokens → VITS runs `pad(..., length-1)` with length=0 →
  `RuntimeError: narrow(): length must be non-negative`. The backend emits lots of Latin-Hinglish
  (`confirm_pending` "Theek hai…", ops `message_hi`), so TTS crashed on exactly those. FIX (in
  `tts._synth_mms`): if `inputs.input_ids.shape[-1]==0`, return silence (debug log, no crash). Devanagari
  + mixed text speak fine (mixed speaks its Devanagari part). Rule-based romanized→Devanagari
  (`indic-transliteration` ITRANS/HK) is NOT a fix — it leaves halants everywhere (`likha`→`लिख्`,
  `stock`→`स्तोच्क्`) and garbles English → clipped speech, worse than silence. Real Hinglish TTS needs
  ai4bharat IndicXlit (a model) — deferred.
- **Force Gradio light mode for a light-only design.** Our paper UI has no dark variant; under an OS dark
  setting Gradio renders the input widgets (audio/image/textbox internals) black/grey on cream. FIX:
  redirect to `?__theme=light` in the `head` `<script>` BEFORE Gradio boots (guarded so no reload loop) +
  `color-scheme: light` in `:root`. Verified by Playwright with `color_scheme="dark"` → `body.dark=False`,
  inputs cream. Restyling individual components for dark is whack-a-mole; force the theme instead.
- **Don't put faux-notebook horizontal rules behind cards.** A `repeating-linear-gradient` ruling on
  `.dk-card`/`.dk-chat` collided with table rows (text sat on lines) and read as clutter. Removed it; the
  red left margin rule (`.dk-card::before`) + paper grain keep the ledger feel without the noise.

## Config: LLM port was decoupled from the client URL (2026-06-09)
- **`DUKAAN_LLM_PORT` did NOT move the client.** `config.LLM_BASE_URL` defaulted to a hardcoded
  `http://127.0.0.1:8080/v1`, independent of `LLM_PORT`. So setting `DUKAAN_LLM_PORT=8081` moved the
  *server* (and even that only via `serve_llm.sh`, which read a *different* var, `PORT`) but the client +
  `llm.health()` still hit 8080 → the UI showed "Gemma offline" with the server actually up on 8081. FIX:
  derive the default `LLM_BASE_URL` from `LLM_HOST`/`LLM_PORT` (`f"http://{LLM_HOST}:{LLM_PORT}/v1"`) and
  make `serve_llm.sh` default `PORT` from `DUKAAN_LLM_PORT` — so one var moves server + client + health.
  `DUKAAN_LLM_BASE_URL` still overrides for a remote endpoint. NOTE: `config.py` reads env/.env at import,
  so the **Gradio app must be restarted** to pick up a changed port (restarting only llama-server isn't enough),
  and the Today banner re-checks health on app load or on the 🔄 Refresh button (it's not auto-polled).
- **Don't change a port and assume health re-checks live.** `dashboard_snapshot()['server_up']` is computed
  per snapshot (not cached), but the Today page renders its banner once at startup — click Refresh (or reload)
  to re-probe after bringing the server up.
- **Surfaced a latent test:** `tests/test_smoke_e2e.py` is `@skipif(not llm.health())`, so it was always
  skipped (server down) and never caught that it predated confirm-before-write. With the client now reachable
  it ran and failed — fixed it to drive the real write-via-confirm flow through the `session` seam.

## TTS alternative-model eval — Parler vs Veena (2026-06-09)
- **MMS can't speak Latin/Hinglish at all** (Devanagari-only vocab → see TTS round-2 note), so we evaluated
  two Hinglish-capable TTS models. Both are **GATED HF repos** (`ai4bharat/indic-parler-tts`,
  `maya-research/veena-tts`, plus `hubertsiuzdak/snac_24khz` for Veena's decoder). The default machine token
  (`rishavk77`) lacks access; an authorized token (account `aadex`) works — `huggingface_hub.login(token=...)`
  writes `~/.cache/huggingface/token` (outside the repo), picked up by both login-node and Slurm jobs.
- **Isolate the eval env — never install into the project venv.** `parler-tts` pins `transformers==4.46.1`;
  the app needs `5.10.1`. Built `~/parler-eval` (uv venv) with torch 2.12+cu130 + transformers 4.46.1 +
  parler-tts 0.2.3 + bitsandbytes + snac. Both Parler AND Veena run on transformers 4.46.1, so one env covers both.
- **bitsandbytes 4-bit (nf4) WORKS on torch 2.12+cu130** (one harmless `_check_is_size` FutureWarning). Veena
  loaded 4-bit in ~20s, ~2-3 GB VRAM. On a 48 GB A6000 bf16 is also fine (the script tries 4-bit, falls back to bf16).
- **Model facts:** Parler = 44.1 kHz, steered by a free-text voice *description* (flan-t5-large encoder + DAC
  codec), ~1× realtime gen. Veena = 24 kHz, Llama-3-style LM emitting SNAC codes, 4 fixed speaker tokens
  (kavya/agastya/maitri/vinaya), ~3-4× realtime gen, explicit code-mixing support. Both speak Hinglish cleanly.
- Repro: `scripts/tts_parler_eval.py`, `scripts/tts_veena_eval.py`, `scripts/tts_eval.sbatch` (combined GPU job).
  Outputs: `logs/parler_*.wav`, `logs/veena_*.wav`. GPU work went through Slurm (job 1368, 3m31s).
- **Chosen + wired Veena as the DEFAULT TTS** (`DUKAAN_TTS_ENGINE=veena`, speaker `agastya`). Key compat
  win: **Veena's `trust_remote_code` class loads fine under the project's `transformers 5.10.1`** — no need
  for the eval env's 4.46.1. Gotchas when integrating into the app venv: (1) load **bf16 + `.to("cuda")`**,
  NOT `device_map="auto"` (that needs `accelerate`, which the app venv lacks); (2) transformers 5.x wants
  `dtype=` not `torch_dtype=`; (3) only new runtime dep is `snac` (pulls `einops`) — added to pyproject;
  bitsandbytes stays optional (`DUKAAN_VEENA_4BIT`). Wired in `tts.py` as `_load_veena`/`_synth_veena`/
  `_decode_snac` with the same fallback chain (veena → mms → silence). Verified end-to-end via
  `tts.synthesize()`: the Latin confirm "Theek hai, kuch nahi likha." now SPEAKS (1.7s) where MMS was silent.

## UI round-3 — broken inputs + chat composer + haptics (2026-06-09)
- **NEVER `display:none` a Gradio `<label>`.** In Gradio 6 the `<textarea>`/`<input>` lives INSIDE its
  `<label>`, so `.dk-input label { display:none !important }` hid the whole control — the Talk textbox
  showed no typed text and the mic/record buttons were invisible ("microphone not detecting"). It also
  silently broke the onboarding text fields. Playwright nailed it: the textarea existed but was
  "not visible". FIX: drop that rule (we already pass `show_label=False`); to hide a stray label caption
  use `span[data-testid="block-info"]:empty { display:none }`. General rule: style the OUTER frame of a
  Gradio input, never `!important`-reset/hide its internals.
- **Use `gr.MultimodalTextbox` for a chat composer.** `sources=["microphone","upload"]` +
  `file_count="multiple"` + `submit_btn=True` gives ONE bar with text · 🎤 Record audio · 📎 attach · ↑ Submit
  (its buttons carry aria-labels "Record audio"/"Upload a file"/"Submit"). Its value is
  `{"text", "files":[paths]}`; mic recordings and image uploads both arrive as file PATHS — route by
  extension (image→`PIL.Image`, audio→`soundfile.read`→`(sr, ndarray)`) into `session.handle_turn`. Clear
  it by returning `{"text":"","files":[]}`. Far more robust than hand-wiring separate audio/image/text widgets.
- **Clickable custom elements need explicit `cursor: pointer`.** `gr.HTML` rows with `data-ask` (and table
  rows) had no pointer cursor → felt dead. Add `[data-ask],[data-page],[data-lang-btn],.dk-tab{cursor:pointer}`
  + a hover background for affordance. `tap-to-ask` JS now targets `#dk-composer` and clicks its
  `button[aria-label="Submit"]` (falls back to dispatching Enter).

## UI round-4 — icons instead of emojis + no em-dash titles (2026-06-09)
- **Swapped all UI emojis for the Phosphor icon webfont** (`<link>` in head.html, `<i class="ph ph-*">`
  via an `ic(name)` helper). Cleaner, monochrome, on-brand (colored brass/red via CSS) — emojis read as
  noisy/childish at this density. `document.fonts.check("16px Phosphor")` confirms it loads; icons inherit
  `currentColor`. Kept: `−` (minus in money), `→`/`⇄` (arrows, not emojis).
- **Bulk emoji→icon replacement is quote-context-sensitive — don't naively string-replace.** Emojis lived in
  THREE contexts, each needing a different substitution: (a) single-quoted f-strings `>💰<` → `{ic("wallet")}`;
  (b) double-quoted button labels `"✅ " + T(...)` → `ic("check") + " " + T(...)`; (c) plain double-quoted
  value literals like `shown = "🎤"` which CANNOT take HTML (they're escaped in the chat bubble) → use plain
  TEXT ("Voice note"). My first pass dropped the space in (b) (`+ " ` instead of `+ " " `) and injected
  `{ic(...)}`/`"` into plain strings — both syntax errors. Lesson: handle the three contexts explicitly and
  PARSE-CHECK after; `ast.parse` caught each break fast.
- **Em dashes in titles → cleaner separators.** Section titles used `—` ("Maal — stock & expiry"); the user
  dislikes em dashes (also an AI-writing tell). Rewrote to plain titles ("Stock & expiry", "Credit ledger",
  "Today's account") and the tagline `—`→`·`. Left `—` only as the "no value" table placeholder.

## UI round-5 — per-message TTS, no checkbox, markdown, long-TTS fix (2026-06-09)
- **Veena truncated long replies at a flat 700-token cap** (~8s ≈ "a sentence or two"). FIX: chunk text by
  sentence (`। . ! ? \n`, ≤160 chars) and concatenate the per-chunk audio with a short pause
  (`_veena_chunks`/`_veena_gen_one` in `tts.py`); per-chunk cap scales with the (bounded) chunk. A 213-char
  line went 8s→13.4s (full).
- **On-demand per-message TTS** instead of a global checkbox: replies are SILENT (`handle_turn(tts=False)`);
  each bot bubble has a speaker icon (`data-speak-idx`). Added `session.speak(text)` and an `app.speak(idx,
  history)` that synthesizes just that message into a hidden, autoplaying `gr.Audio`.
- **Gradio 6 DROPS `visible=False` components from the DOM** — a JS bridge can't reach them. Use a real
  component with `elem_classes=["dk-hidden"]` (`display:none` in CSS) so it's rendered but invisible.
- **JS→Gradio "set value then trigger" has a state-commit race.** A synthetic `input` event did NOT fire
  Gradio's `.input` handler; and clicking a hidden button 50ms after setting a textbox sent the STALE value.
  What worked: set the textbox value (native setter) + dispatch `input`+`change`+`blur`, then click a hidden
  `gr.Button` after ~300ms (it reads the now-committed value). Add a `|<nonce>` to the value so repeat clicks
  always change it.
- **Gradio 6 audio uses a wavesurfer player, NOT a plain `<audio src>`.** Verifying via `<audio>.src` is a
  red herring (always empty); check the rendered download link `…/gradio_api/file=/tmp/gradio/*.wav` to
  confirm the audio reached the frontend. `autoplay=True` + the user's click gesture plays it.
- **Light markdown for bot replies** via markdown-it-py (`html=False` so raw HTML is escaped = safe,
  `breaks=True`); wrapped in `.dk-md` with reset `<p>`/list margins. Falls back to escaped text if absent.

## UI round-6 — real streaming + markdown headings/tables (2026-06-09)
- **Real token streaming through the deepagents/LangGraph agent.** `agent.stream(stream_mode="messages")`
  yields `(chunk, meta)`; filter `chunk.__class__.__name__ == "AIMessageChunk"` and accumulate
  `_content_to_text(chunk.content)` to get the FINAL answer token-by-token — `ToolMessage` chunks carry tool
  output and must be EXCLUDED (they'd leak raw tool results into the bubble). After the stream, read
  `agent.get_state(cfg).values["messages"]` for the authoritative reply + tool_calls + pending. Added
  `agent.stream_agent` (yields `("delta", acc)` … `("final", {...})`).
- **Seam: `session.handle_turn_stream` generator** mirrors `handle_turn`. Refactored the shared pre-agent
  work (STT / OCR / empty / yes-no confirm) into `_prepare_turn(...)` which returns either a finished
  `TurnResult` (short-circuit) or `(user_text, detected_lang)` — so both the streaming and non-streaming
  paths stay identical. `handle_turn` is unchanged behaviourally (e2e test still green).
- **UI: `respond` consumes the stream**, updating a bot placeholder bubble each delta. Keep the typing dots
  until the first token (`typing = not text`); refresh the dashboard only on the final (snapshot is empty on
  deltas); throttle renders to ~every 5 chars to cut chat re-renders (41 tokens → ~7 paints). Verified: bubble
  grew 40→99→…→651 chars live.
- **Markdown headings looked identical** because h1/h2/h3 all shared one size/weight. Give a real scale:
  h1 1.4em, h2 1.2em + bottom border, h3 1.02em uppercase+muted, **bold** = body size but weight 800 + ink
  colour. **Tables**: commonmark preset OMITS them — `MarkdownIt("commonmark", …).enable("table")` — then
  style `.dk-md table/th/td` (header band, borders, zebra).

## UI round-7 — progress status with the dots (2026-06-09)
- **Show what the agent is doing before the answer streams.** Tool calls (a DB read/write) take a beat with
  no user-facing text; bare dots felt dead. `stream_agent` now also detects tool activity — an
  `AIMessageChunk.tool_call_chunks` entry (name) signals a tool is being called; classify `write` vs `read`
  (via `_WRITE_TOOLS`) and yield `("status", code)`. `ToolMessage.name` is the reliable fallback. Threaded a
  `status` field on `TurnResult`; the UI maps `read`→"Checking the books", `write`→"Writing to the books",
  else "Thinking", and shows it in `.dk-typing__t` next to the dots. Verified live: dots progressed
  "Thinking…" → "Checking the books…" → streamed answer.
- **Don't add the empty bot bubble during the status phase** — render the dots+label INSTEAD; only append the
  bot message once real answer text arrives (else an empty bubble flashes under the dots).

## UI round-8 — auto-scroll the chat while streaming (2026-06-09)
- **Gradio replaces a `gr.HTML`'s innerHTML on every update**, so the streamed chat's `.dk-chat` scroll area
  is recreated each token with `scrollTop=0` → the view sticks to the top. Fix: a `MutationObserver` on the
  PERSISTENT panel root (`elem_id="dk-chat-panel"`, which Gradio keeps; only its contents change) that, on any
  childList/subtree mutation, re-pins the inner `.dk-chat` to `scrollHeight` (batched via `requestAnimationFrame`).
  Also call it on tab-switch to Talk. Verified: dist-from-bottom stayed 0 across a full streamed reply.
- Gotcha while testing: a short reply that fits the 460px box has `scrollHeight == clientHeight` (no overflow,
  nothing to scroll) — to actually test auto-scroll you must force overflow (long reply / multiple messages).
