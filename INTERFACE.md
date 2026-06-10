# Dukaan Saathi — Backend→UI Interface Contract

This document is the handoff reference for building the Gradio front-end and Hugging Face Space on top of the Dukaan Saathi backend. You should NOT need to touch any backend logic.

---

## 1. Overview

The backend is a self-contained Python package (`dukaan/`). It handles:

- Voice transcription (STT), image OCR, LLM agent, TTS synthesis
- All inventory, sales, udhaar/khata writes (FEFO lots, merge dedup)
- Onboarding FSM (first-run setup replacing the demo seed)
- Challan photo parsing and staged receive
- Dashboard analytics and morning briefings

**The UI layer must only import from three modules:**

```python
import dukaan.session      # per-turn conversation + dashboard + briefing
import dukaan.onboarding   # first-run FSM
import dukaan.receiving    # challan photo -> editable preview -> commit
```

**The UI must never call `dukaan.stt`, `dukaan.agent`, `dukaan.ops`, `dukaan.tts`, or `dukaan.tools` directly.** All heavy deps are wired together inside `session.handle_turn`; the UI only renders the returned dataclasses/dicts.

---

## 2. Per-Turn Conversation

### `session.handle_turn()`

```python
from dukaan import session

result: TurnResult = session.handle_turn(
    audio=(sample_rate, np_array) | None,   # from gr.Audio(type="numpy")
    text="..." | None,                       # from gr.Textbox
    image=PIL.Image | None,                  # from gr.Image(type="pil")
    thread_id="user-123",                    # stable per-session ID (use gr.State)
    tts=True,                                # True = synthesize reply_audio
    mode="auto",                             # reserved; pass "auto"
)
```

Exactly one of `audio` or `text` should be provided (both is fine; audio wins). `image` is optional and can accompany either.

### `TurnResult` fields

| Field | Type | Render as |
|---|---|---|
| `reply_text` | `str` | Chat bubble (bot side) |
| `reply_language` | `str` | Language chip (e.g. `"hi"`) — future use |
| `reply_audio` | `(int, np.ndarray) \| None` | `gr.Audio(value=result.reply_audio, autoplay=True)` |
| `detected_language` | `str` | `"Heard: hi"` chip above user message |
| `pending_confirmation` | `PendingConfirmation \| None` | Yes/No buttons when not `None` (see below) |
| `clarification` | `str \| None` | Highlight STT/OCR retry prompt; re-activate input |
| `needs_reupload` | `bool` | Highlight/flash image upload widget when `True` |
| `dashboard_snapshot` | `dict` | Refresh side-panel (see Section 3) |
| `intent_badge` | `str` | Small badge: `"sale"`, `"restock"`, `"udhaar"`, `"query"`, `"write"`, `"chat"` |
| `tool_calls` | `list` | Optional debug panel (list of tool-call dicts) |
| `user_text` | `str` | Echo back above the chat bubble: `"You said: <text>"` |
| `error` | `str \| None` | Show non-blocking warning toast when not `None` |

`reply_audio` is `None` when `tts=False` or TTS fails (non-fatal). The UI should always fall back gracefully to `reply_text`.

### `PendingConfirmation` fields

```python
@dataclass(frozen=True)
class PendingConfirmation:
    token: str            # internal staging batch_id (opaque to UI)
    kind: str             # "commit_write" (only kind today)
    prompt: str           # Hindi summary to show in the confirmation modal
    options: list[str]    # [] today; future: ["Haan", "Nahi"]
    payload: dict         # {} today; future: additional context
```

When `pending_confirmation` is set, render a confirmation bar or modal:

```
<prompt text>
[ Haan (Yes) ]   [ Nahi (No) ]
```

On click, call:

```python
result = session.confirm_pending(
    answer="haan" | "nahi",   # or "yes"/"no"/"cancel"/"theek hai"
    thread_id=thread_id,
    tts=True,
)
# result is a TurnResult — render reply_text / reply_audio as usual
```

`confirm_pending` returns a `TurnResult` (intent_badge `"write"`). If the answer is ambiguous ("pata nahi", etc.), `reply_text` will ask again — keep the Yes/No buttons visible.

---

## 3. Dashboard + Morning Briefing

### `session.dashboard_snapshot_struct()`

```python
snap: dict = session.dashboard_snapshot_struct()
```

Returns a dict — every key is best-effort and may be absent on error:

```python
{
    "stock_value": {
        "at_cost": float,          # total stock value at purchase price
        "at_mrp": float,           # total stock value at MRP
        "potential_margin": float,
        "total_units": int,
        "item_count": int,
    },
    "today": {
        "revenue": float,
        "units": int,
        "num_sales": int,
        "top_items": [{"name": str, "qty": int, "revenue": float}, ...],
    },
    "expiring": [                  # items expiring within EXPIRY_WARN_DAYS (default 15)
        {"item_id": int, "name": str, "category": str, "qty": int,
         "expiry_date": str,       # ISO date
         "days_left": int,
         "is_estimated": bool},    # True = expiry was inferred, not from challan
        ...
    ],
    "low_stock": [
        {"item_id": int, "name": str, "category": str, "qty": int,
         "reorder_level": int},
        ...
    ],
    "udhaar": {
        "total": float,
        "count": int,
        "customers": [
            {"customer_id": int, "name": str, "phone": str|None,
             "balance": float, "earliest_due": str|None, "overdue": bool},
            ...
        ],
    },
    "slow_movers": [               # items with stock but no sales in last SLOW_MOVER_DAYS
        {"item_id": int, "name": str, "category": str, "qty": int,
         "last_sold": str|None, "sold_recent": int},
        ...
    ],
    "festival": str | None,        # Hindi nudge about upcoming festival, or None
    "server_up": bool,             # LLM server health (False = offline mode)
    "error": str,                  # present ONLY on total failure
}
```

`dashboard_snapshot` is also embedded in every `TurnResult.dashboard_snapshot` so the panel auto-refreshes after each turn.

### `session.morning_briefing(tts=False)`

```python
briefing = session.morning_briefing(tts=False)
# {
#   "text": str,   # ~50-word Hindi summary suitable for gr.Markdown or gr.Textbox
#   "audio": (int, np.ndarray) | None,  # only when tts=True
#   "parts": {
#       "expiry": {"message": str},
#       "udhaar": {"message": str},
#       "festival": {"message": str},
#   }
# }
```

Show on app load (or on a "Subah ka haal" button). Render `briefing["text"]` as a greeting card; play `briefing["audio"]` if TTS was requested.

---

## 4. Onboarding Wizard

### Routing

```python
from dukaan import onboarding

if onboarding.is_onboarding_active():
    # show onboarding UI
else:
    # show normal dashboard + chat
```

`is_onboarding_active()` returns `True` only when `db.data_mode() == "demo"` AND a non-terminal session exists. Once `confirm_commit()` succeeds, data_mode flips to `"real"` and this returns `False` forever.

### FSM States

| State | step_index | Meaning |
|---|---|---|
| `profile` | 0 | Collect owner name, shop name, language |
| `rough_inventory` | 1 | Voice / photo / manual item capture (repeat freely) |
| `khata` | 2 | Optional: photo of handwritten khata |
| `verify` | 3 | Review + edit/delete all drafts before committing |
| `committing` | 3 | In-progress (brief; UI shows spinner) |
| `done` | 4 | Terminal — session forgotten |
| `aborted` | 4 | Terminal — session forgotten |

### Call Sequence

```python
# 1. Start (or resume after a page reload)
view = onboarding.start_onboarding(resume=True)

# 2. Collect profile
view = onboarding.set_profile(owner_name, shop_name, language)
# -> advances to rough_inventory on success; ok=False + message on missing fields

# 3a. Capture stock — voice
view = onboarding.capture_inventory_voice(audio=(sr, np_array), language="hi")

# 3b. Capture stock — photo of shelf / handwritten list
view = onboarding.capture_inventory_photo(image=pil_image)

# 3c. Capture stock — manual entry (always available alongside voice/photo)
view = onboarding.add_inventory_item_manual(name, qty, category="", mrp=0.0)

# 4. (Optional) Capture khata photo
view = onboarding.capture_khata_photo(image=pil_image)

# 5. Move to VERIFY screen
view = onboarding.advance_to_verify()

# 6. Edit / delete while on VERIFY (or during capture)
view = onboarding.edit_draft_row(kind="item"|"customer", row_id="it-3", patch={"qty": 12})
view = onboarding.delete_draft_row(kind="item"|"customer", row_id="it-3")

# 7. Commit
result = onboarding.confirm_commit()
# -> {ok: True, message_hi: str, summary: {items: int, customers: int}}
# -> or {ok: False, error: str, ...} on failure (session rolled back to VERIFY)

# Abort at any point
onboarding.abort_onboarding(keep_demo=True)
```

Steps 3a/3b/3c can be called any number of times to accumulate items. Calls in any order are fine; items/customers append to the draft lists without limit.

### Step-View Dict

Every call (except `confirm_commit` and `abort_onboarding`) returns a step-view:

```python
{
    "ok": bool,
    "state": str,         # OnboardingState value
    "step_index": int,    # 0-4 — drive a stepper widget
    "drafts": {
        "items": [
            {"id": "it-N", "name": str, "qty": int, "category": str,
             "mrp": float, "est_expiry": str|None,
             "confidence": "high"|"low", "source": "manual"|"voice"|"photo"},
            ...
        ],
        "customers": [
            {"id": "cu-N", "name": str, "phone": str, "opening_balance": float,
             "debits": [{"amount": float, "items": str|None, "date": str|None}],
             "confidence": "high"|"low", "source": "manual"|"photo"},
            ...
        ],
    },
    "needs": None | "repeat" | "reupload",
    "prompt": str | None,    # retry message (Hindi + English) — always non-empty when needs is set
    "message": str | None,   # error detail (ok=False cases)
}
```

**Never-empty rule:** when `needs == "repeat"` or `needs == "reupload"`, `prompt` is always a non-empty bilingual string. Show it prominently and re-enable the relevant input (mic or file upload).

### VERIFY View

`advance_to_verify()`, `get_verify_view()`, and edits while in VERIFY return the VERIFY shape instead of the standard step-view:

```python
{
    "ok": bool,
    "state": "verify",
    "step_index": 3,
    "items": [...],        # same ItemRow shape as drafts.items
    "customers": [...],    # same CustomerRow shape as drafts.customers
    "totals": {
        "item_count": int,
        "total_units": int,
        "customer_count": int,
        "opening_balance_total": float,
    },
}
```

Render this as an editable table with inline edit/delete per row (call `edit_draft_row` / `delete_draft_row` on change). Show `totals` as a summary bar. A "Confirm" button calls `confirm_commit()`.

---

## 5. Challan Receive

Three-call contract: parse -> (UI edits) -> commit.

### Step 1: Parse

```python
from dukaan import receiving

preview = receiving.stage_receive(
    image=pil_image,           # challan photo (pass image= OR lines=, not both)
    supplier_hint="Ramesh",    # optional; extracted from OCR if absent
)
# or, after the user edited names/qtys, re-stage with the edited list:
preview = receiving.stage_receive(lines=[...edited items...])
```

`stage_receive` is **read-only** — no DB writes.

`preview` shape:

```python
{
    "ok": bool,
    "error": None | "reupload",
    "supplier": str | None,
    "date": str | None,           # ISO date from challan
    "total_cost": float,
    "needs_confirmation": True,   # always True on ok=True
    "items": [
        {
            "input_name": str,        # raw OCR text
            "resolved_name": str,     # matched inventory name (or same as input_name)
            "item_id": int | None,    # set when action="merge"
            "action": "merge"|"new",  # merge = restock existing; new = create on commit
            "qty": int,
            "unit": str,
            "rate": float | None,     # purchase price per unit
            "mrp": float | None,
            "hsn": str | None,
            "estimated_expiry": str | None,  # ISO date (from shelf-life if challan omits)
            "is_estimated": bool,            # True = expiry was inferred
            "candidates": [str, ...],        # alternative name matches for disambiguation UI
        },
        ...
    ],
    "message": str,               # Hindi readback e.g. "Ramesh se 8 cheezein aayi, kul ₹1240 — daal dun?"
}
```

When `ok=False` and `error=="reupload"`, show `message` and re-enable the file upload.

### Step 2: UI Edits (no API call)

Render `preview["items"]` as an editable table. The owner corrects names (use `candidates` to show a dropdown), adjusts qty/rate, removes lines. When done, pass the edited list back to `stage_receive(lines=...)` for a fresh resolve, or proceed directly to commit.

### Step 3: Commit

```python
result = receiving.commit_receive(
    staged_items=preview["items"],  # edited list
    supplier="Ramesh",
)
# {
#   "ok": bool,
#   "received": [list of record_purchase results for successes],
#   "failed":   [list of record_purchase results for failures],
#   "total_cost": float,
#   "message_hi": str,   # e.g. "8 cheezein stock me daal di, kul ₹1240."
# }
```

Show `result["message_hi"]` as a toast. If `ok=False` (partial failure), show `failed` rows highlighted so the owner can re-check and retry.

---

## 6. Config / Environment Knobs

All values live in `dukaan/config.py` and are overridable via environment variable or a `.env` file at the project root.

| Env var | Default | Notes |
|---|---|---|
| `DUKAAN_DATA_DIR` | `<repo>/data` | **Point at the Space `/data` persistent volume** so both SQLite DBs and the onboarding session survive Space restarts |
| `DUKAAN_INVENTORY_DB` | `$DATA_DIR/inventory.db` | Override path for the inventory database |
| `DUKAAN_TRANSACTIONS_DB` | `$DATA_DIR/transactions.db` | Override path for the transactions/khata database |
| `DUKAAN_LLM_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible endpoint from `llama-server` |
| `DUKAAN_LLM_MODEL` | `gemma-4-12b` | Model name passed to the endpoint |
| `DUKAAN_GEMMA_GGUF` | `models/gemma4/gemma-4-12B-it-Q8_0.gguf` | GGUF file for `scripts/serve_llm.sh` |
| `DUKAAN_GEMMA_MMPROJ` | `models/gemma4/mmproj-gemma-4-12B-it-Q8_0.gguf` | Multimodal projector GGUF |
| `DUKAAN_WHISPER_MODEL` | `large-v3` | faster-whisper model name or HF repo |
| `DUKAAN_STT_LANGUAGE` | `""` (auto) | Force a language code (e.g. `"hi"`) to skip auto-detect |
| `DUKAAN_STT_HINDI_MODEL` | `digikar/vasista22-whisper-hindi-large-v2-ct2-int8` | Hindi-optimized 2nd-pass model (Apache-2.0, not gated). Downloaded on first use via faster-whisper |
| `DUKAAN_TTS_ENGINE` | `veena` | `"veena"` (maya-research/veena-tts — Hindi/English/**Hinglish** via a SNAC decoder; gated HF repo, needs an authorized token + the `snac` package), `"mms"` (facebook/mms-tts-hin, fast but **Devanagari-only — silent on Latin**), or `"parler"` (ai4bharat/indic-parler-tts, gated, needs `parler-tts`). Any failure falls back to mms, then to silence. |
| `DUKAAN_VEENA_SPEAKER` | `agastya` | Veena voice: `kavya` \| `agastya` \| `maitri` \| `vinaya` |
| `DUKAAN_VEENA_4BIT` | `false` | `true` loads Veena in 4-bit (needs `bitsandbytes`+`accelerate`, ~2-3 GB VRAM); default is bf16 (~6 GB) |
| `DUKAAN_VEENA_MODEL` / `DUKAAN_VEENA_SNAC` | `maya-research/veena-tts` / `hubertsiuzdak/snac_24khz` | Veena LM + its SNAC neural-audio decoder |
| `DUKAAN_MMS_MODEL` | `facebook/mms-tts-hin` | HF repo for the MMS fallback TTS |
| `DUKAAN_CONFIRM_WRITES` | `true` | Set to `false` to skip the yes/no confirmation step and commit immediately (not recommended for demo) |
| `DUKAAN_GRADIO_HOST` | `0.0.0.0` | Gradio server bind address |
| `DUKAAN_GRADIO_PORT` | `7860` | Gradio server port |

**One-time model download:** On first run with STT enabled, faster-whisper will download `DUKAAN_WHISPER_MODEL` (and `DUKAAN_STT_HINDI_MODEL` if non-empty). Pre-download in the Dockerfile / Space setup script to avoid cold-start delays:

```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu')"
```

---

## 7. Demo vs Real Data

```python
from dukaan import db

mode = db.data_mode()   # "demo" | "real"
```

- `"demo"` — seeded synthetic kirana data is shown. Onboarding not yet completed.
- `"real"` — owner's actual data (set permanently after `onboarding.confirm_commit()`).

The demo seed is the fallback; it is never shown again once the owner completes onboarding. `db.backfill_lots()` is called automatically by `db.init_db()` to ensure all inventory rows have FEFO lots.

---

## 8. Running Locally

```bash
# 1. Start the LLM server (llama.cpp llama-server; blocks this terminal)
bash scripts/serve_llm.sh

# 2. In a second terminal, launch the Gradio app
uv run python -m dukaan.app

# 3. Run the test suite (no GPU/server required — all heavy deps are stubbed)
uv run python -m pytest tests/ -q
```

**Language note:** Replies from the agent are currently Hindi / Hinglish. `DUKAAN_REPLY_LANG_MODE` is the future switch for multilingual replies (`"same_as_user"` mode); it has no effect in the current backend and is a planned feature.

**Hugging Face Space setup checklist:**

1. Set `DUKAAN_DATA_DIR=/data` in Space secrets so the DBs persist across restarts.
2. Pre-download Whisper **and the default Veena TTS** in the Dockerfile (`bash scripts/download_models.sh`). Veena (`maya-research/veena-tts` + `hubertsiuzdak/snac_24khz`) is **gated** — add an authorized `HF_TOKEN` Space secret so the download succeeds; without it the app falls back to MMS (Devanagari-only, silent on Hinglish). Set `DUKAAN_VEENA_4BIT=true` on small-GPU Spaces (~2-3 GB) or keep bf16 (~6 GB).
3. The LLM server must run as a sidecar process (`scripts/serve_llm.sh`) — start it in `app.py` via `subprocess.Popen` before launching Gradio, or provision it as a separate Space endpoint.
4. Use `DUKAAN_GRADIO_SHARE=false`; Spaces handles public access.
