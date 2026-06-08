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
