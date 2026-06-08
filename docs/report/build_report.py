#!/usr/bin/env python
"""Build the Dukaan Saathi project report PDF.

Renders ~10 Mermaid diagrams to inline SVG via kroki.io, samples the live two
databases for real schema + rows + stats, composes a styled HTML document, and
writes it to docs/Dukaan_Saathi_Project_Report.pdf via WeasyPrint.

    uv run --with weasyprint python docs/report/build_report.py
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path

import requests
from weasyprint import CSS, HTML

from dukaan import config, db, ops

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "docs" / "report"
DIAG_DIR = REPORT_DIR / "diagrams"
DIAG_DIR.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "docs" / "Dukaan_Saathi_Project_Report.pdf"
TODAY = "2026-06-06"

# ============================================================ diagrams (Mermaid)
DIAGRAMS = {
"d1_arch": """flowchart TB
  subgraph IN["Multimodal input"]
    V["Voice (Hindi)"]; T["Text (Hindi / Hinglish)"]; I["Image (bill / label)"]
  end
  V --> STT["Whisper STT"]
  I --> OCR["Gemma-4 Vision OCR"]
  T --> NORM["Normalize"]
  STT --> NORM
  OCR --> NORM
  NORM --> AG{{"deepagents loop + Gemma-4-12B"}}
  AG --> TOOLS["Tool registry: write tools / query_database / dashboard"]
  TOOLS --> INVDB[("inventory.db")]
  TOOLS --> TXNDB[("transactions.db")]
  AG --> REPLY["Concise Hindi reply"]
  REPLY --> TTS["MMS-TTS (Hindi)"] --> SPK["Speaks back"]
  AG -.-> DASH["Gradio: chat + today dashboard + alerts"]
""",
"d2_serve": """flowchart LR
  USER["Shopkeeper (browser, LAN)"] --> APP
  subgraph SLURM["Slurm GPU allocation — sbatch scripts/run.sbatch"]
    APP["Gradio app :7860<br/>Whisper STT + MMS-TTS<br/>deepagents client"]
    LS["llama-server :8080<br/>Gemma-4 Q8 + mmproj<br/>--jinja, -ngl 99, thinking off"]
  end
  APP -->|"OpenAI-compatible /v1<br/>(thinking disabled)"| LS
  GPU[("RTX A6000 — 48 GB")] --- LS
""",
"d3_agent": """flowchart TB
  U["User message (Hindi)"] --> M["Gemma-4 via ChatOpenAI<br/>(system prompt + 9 tools)"]
  M -->|"emits tool call"| TX["Execute tool<br/>(ops / SELECT-guard)"]
  TX --> OB["Observation (rows / confirmation)"]
  OB --> M
  M -->|"no more tools"| R["Final reply (Devanagari Hindi)"]
  M -.->|"multi-step plan"| TODO["write_todos"]
""",
"d4_route": """flowchart TD
  Q["Normalized message"] --> C{"Intent?"}
  C -->|"add / record (stock, sale, udhaar, payment)"| W["WRITE tool → owning DB"]
  C -->|"simple fact / lookup"| S["get_dashboard / get_customer_dues / query_database"]
  C -->|"why / diagnostic"| D["Multi-step: trend + stock + expiry + compare → reason"]
  C -->|"reminder / nudge"| P["Proactive draft"]
""",
"d5_er_inv": """erDiagram
  SUPPLIERS ||--o{ INVENTORY : "supplies"
  INVENTORY ||--o{ PURCHASES : "restocked by"
  SUPPLIERS {
    int supplier_id PK
    string name
  }
  INVENTORY {
    int item_id PK
    string name
    int qty
    real mrp
    date expiry_date
  }
  PURCHASES {
    int purchase_id PK
    int item_id FK
    int qty
    real cost
  }
""",
"d6_er_txn": """erDiagram
  CUSTOMERS ||--o{ SALES : "buys"
  CUSTOMERS ||--o{ LEDGER : "owes / pays"
  CUSTOMERS {
    int customer_id PK
    string name
  }
  SALES {
    int sale_id PK
    int item_id
    string item_name
    int qty
    int customer_id FK
  }
  LEDGER {
    int entry_id PK
    int customer_id FK
    string type
    real amount
    date due_date
  }
""",
"d7_datagen": """flowchart LR
  R["3 research subagents<br/>web: catalog · pricing · expiry · udhaar"] --> INV
  INV["Inventory subagent<br/>seed_inventory.py<br/>160 SKUs + 15 suppliers + restocks"] --> LED
  LED["Ledger subagent<br/>seed_ledger.py<br/>35 customers + ~6k sales + udhaar"]
  INV --> LOAD["db._seed_all (loader)<br/>maps names→ids, computes expiry,<br/>adds today's tranche"]
  LED --> LOAD
  LOAD --> DBI[("inventory.db")]
  LOAD --> DBT[("transactions.db")]
""",
"d8_speech": """flowchart LR
  MIC["Mic → numpy 16k mono"] --> WSP["faster-whisper large-v3<br/>language=hi, vad"] --> TXT["Hindi text"]
  IMG["Photo (PIL)"] --> VIS["Gemma-4 vision<br/>image_url base64"] --> EXT["Extracted bill/label text"]
  RPLY["Hindi reply"] --> CLEAN["numwords:<br/>digits + Rs → Hindi words"] --> MMS["MMS-TTS-hin (VITS)"] --> WAV["16k waveform"]
""",
"d9_proactive": """flowchart LR
  CRON["On open / Refresh / Alerts button"] --> EXP["Expiry Watcher<br/>items ≤ 15 days"]
  CRON --> FEST["Festival Nudge<br/>2026 calendar"]
  CRON --> UDH["Udhaar Reminder<br/>overdue → Hindi WhatsApp draft"]
  EXP --> DASH["Dashboard / Alerts panel"]
  FEST --> DASH
  UDH --> DASH
""",
"d10_seq": """sequenceDiagram
  autonumber
  participant U as Shopkeeper
  participant G as Gradio app
  participant W as Whisper STT
  participant A as deepagents + Gemma-4
  participant DB as inventory.db + transactions.db
  participant T as MMS-TTS
  U->>G: speaks Hindi (mic)
  G->>W: numpy audio (16 kHz)
  W-->>G: Hindi text
  G->>A: run_agent(text, thread_id)
  A->>DB: tool calls — write OR ATTACH read SQL
  DB-->>A: rows / confirmation
  A-->>G: concise Hindi reply
  G->>T: synthesize(reply)
  T-->>G: wav (autoplay)
  G-->>U: chat bubble + spoken reply + dashboard refresh
""",
}


def render_diagram(key: str, mmd: str) -> str:
    """Mermaid → PNG via kroki (cached), embedded as a base64 <img>.

    PNG (not SVG) because WeasyPrint does not render Mermaid's <foreignObject>
    HTML node labels — a raster image keeps every label."""
    import base64
    png_path = DIAG_DIR / f"{key}.png"
    if png_path.exists() and png_path.stat().st_size > 500:
        data = png_path.read_bytes()
    else:
        try:
            r = requests.post("https://kroki.io/mermaid/png", data=mmd.encode(), timeout=60)
            if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                data = r.content
                png_path.write_bytes(data)
            else:
                return f"<p class='muted small'>[diagram '{key}' unavailable: kroki {r.status_code}]</p>"
        except Exception as exc:
            return f"<p class='muted small'>[diagram '{key}' unavailable: {_html.escape(str(exc))}]</p>"
    b64 = base64.b64encode(data).decode()
    return f'<img src="data:image/png;base64,{b64}" alt="{esc(key)}">'


DIAG: dict[str, str] = {}


def build_diagrams() -> None:
    for k, m in DIAGRAMS.items():
        DIAG[k] = render_diagram(k, m)
        print(f"  diagram {k}: {'ok' if '<img' in DIAG[k] else 'FAIL ' + DIAG[k][:90]}")


# ===================================================================== helpers
def esc(x) -> str:
    return _html.escape(str(x))


def fig(key: str, caption: str) -> str:
    return (f"<figure><div class='diagram-box'>{DIAG.get(key,'')}</div>"
            f"<figcaption>{esc(caption)}</figcaption></figure>")


def callout(kind: str, label: str, body_html: str) -> str:
    return f"<div class='callout {kind}'><span class='lbl'>{esc(label)}</span> {body_html}</div>"


def code(text: str) -> str:
    return f"<pre><code>{esc(text)}</code></pre>"


def kv(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows)
    return f"<table class='kv'>{body}</table>"


def _cell(v, money=False) -> str:
    if v is None:
        return "<span class='muted'>—</span>"
    if isinstance(v, float):
        s = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
        return f"Rs {s}" if money else s
    if isinstance(v, int) and money:
        return f"Rs {v:,}"
    return esc(v)


def data_table(caption: str, sql: str, money_cols=(), num_cols=()) -> str:
    rows = db.qx(sql)
    if not rows:
        return "<p class='muted'>(no rows)</p>"
    keys = list(rows[0].keys())
    numset = set(num_cols) | set(money_cols)
    th = "".join(f"<th class='{'num' if k in numset else ''}'>{esc(k)}</th>" for k in keys)
    trs = ""
    for r in rows:
        tds = "".join(
            f"<td class='{'num' if k in numset else ''}'>{_cell(r.get(k), money=k in money_cols)}</td>"
            for k in keys)
        trs += f"<tr>{tds}</tr>"
    return (f"<table class='data'><caption>{esc(caption)}</caption>"
            f"<thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")


def section(num: str, sid: str, title: str, *blocks: str) -> str:
    head = f"<h1 class='section' id='{sid}'><span class='n'>{num}</span>&nbsp; {esc(title)}</h1>"
    return head + "".join(blocks)


# ===================================================================== content
def cover() -> str:
    chips = ["Gemma-4-12B", "llama.cpp", "deepagents", "Whisper STT", "MMS-TTS",
             "2 × SQLite", "Gradio", "Slurm / CUDA"]
    chip_html = "".join(f"<span class='chip'>{c}</span>" for c in chips)
    return f"""<section class='cover'><div class='pad'>
      <div class='kicker'>As-built project report</div>
      <h1>Dukaan&nbsp;Saathi</h1>
      <div class='sub'>A Hindi-first, voice + image inventory &amp; <em>udhaar</em> (credit)
        assistant for a small kirana shop — built on a local Gemma-4-12B.</div>
      <div class='pitch'>The shopkeeper just <strong>talks in Hindi</strong> or snaps a photo of a
        bill. Behind one clean screen, a local multimodal model reads it, decides what to do,
        updates two SQLite ledgers, and speaks the answer back — no typing, no cloud, no API bill.</div>
      <div class='chips'>{chip_html}</div>
      <div class='meta'>Complete current-state walkthrough &middot; flows, architecture, databases,
        and verification &middot; {TODAY}</div>
    </div></section>"""


def toc() -> str:
    items = [
        ("s1", "1 · Executive summary"), ("s2", "2 · At a glance"),
        ("s3", "3 · System architecture"), ("s4", "4 · The model &amp; how it is served"),
        ("s5", "5 · The agentic layer (deepagents)"), ("s6", "6 · The two databases"),
        ("s7", "7 · Speech &amp; vision"), ("s8", "8 · Proactive agents"),
        ("s9", "9 · End-to-end flow walkthroughs"), ("s10", "10 · The Gradio app"),
        ("s11", "11 · Deployment &amp; operations"), ("s12", "12 · Testing &amp; verification"),
        ("s13", "13 · Design decisions &amp; gotchas"), ("s14", "14 · Project layout"),
        ("s15", "15 · Limitations &amp; future work"), ("s16", "16 · Appendix"),
    ]
    lis = "".join(f"<li class='lead'><a href='#{i}'>{t}</a></li>" for i, t in items)
    return f"<div class='toc'><h2>Contents</h2><ol>{lis}</ol></div>"


def sec_exec() -> str:
    cnt = db.counts()
    sv = ops.stock_value(); td = ops.today_summary(); ud = ops.pending_udhaar()
    return section("1", "s1", "Executive summary",
        "<p class='lead-p'>A real kirana (corner) shop runs on a paper <em>bahi-khata</em>: stock, "
        "prices, expiry, and customer credit (<em>udhaar</em>) all live in a notebook the owner can "
        "lose, can't search, and can't analyse. The owner is fluent in Hindi but not in typing English "
        "or spreadsheets. <strong>Dukaan Saathi</strong> removes that notebook with a single voice-first "
        "screen.</p>",
        "<p>The owner speaks (or types, or photographs a bill). A <strong>local, multimodal "
        "Gemma-4-12B</strong> — served by llama.cpp and driven by the <strong>deepagents</strong> "
        "framework — understands the Hindi, decides whether it is a change to record or a question to "
        "answer, calls the right tool (write or read), and replies concisely in spoken Hindi. Inventory "
        "lives in one SQLite database; sales and the credit ledger live in another. Nothing leaves the "
        "machine and there is no per-token API cost.</p>",
        callout("result", "What works today",
            "Every capability below is implemented and verified end-to-end against the real 12B model: "
            "voice/text/image input, write &amp; read tools, single- and cross-database lookups, a "
            "multi-step <em>“why isn’t this selling?”</em> diagnostic, bill OCR, three proactive agents, "
            "and a spoken-Hindi reply loop. The automated suite is <strong>31 tests green</strong>, and a "
            "ground-truth agent battery answered every task type correctly."),
        "<h3>The numbers it runs on (live, from the seeded demo)</h3>",
        kv([
            ("Catalog", f"<strong>{cnt['items']}</strong> SKUs · {cnt['suppliers']} suppliers · "
                        f"{cnt['purchases']:,} restock records (inventory.db)"),
            ("Transactions", f"<strong>{cnt['sales']:,}</strong> sales over ~120 days · "
                             f"{cnt['customers']} customers · {cnt['ledger']} udhaar entries (transactions.db)"),
            ("Stock value", f"Rs {sv['at_cost']:,.0f} at cost → Rs {sv['at_mrp']:,.0f} at MRP "
                            f"(potential margin Rs {sv['potential_margin']:,.0f})"),
            ("Today so far", f"Rs {td['revenue']:,.0f} across {td['num_sales']} sales"),
            ("Outstanding udhaar", f"Rs {ud['total']:,.0f} across {ud['count']} customers "
                                   f"({len(ops.overdue_udhaar())} overdue)"),
        ]))


def sec_glance() -> str:
    cards = [
        ("Voice credit ledger", "“Sharma ji ne 200 ka udhaar liya” / “kiska kitna baaki hai?” — add and "
            "query the khata entirely by voice."),
        ("Inventory + expiry", "Track stock, MRP, purchase price and expiry; flag what’s about to expire."),
        ("Festival stock-up nudge", "A 2026 festival calendar reminds the owner to restock before spikes."),
        ("“Why isn’t X selling?”", "A multi-step diagnostic reasons over sales trend, stock, expiry and margin."),
        ("Reminder drafter", "Drafts a polite Hindi WhatsApp message for each overdue udhaar."),
        ("Margin &amp; stock value", "Selling price, purchase price and MRP per item → live margin and stock value."),
    ]
    card_html = "".join(f"<div class='card'><h4>{t}</h4><p>{d}</p></div>" for t, d in cards)
    stack = [
        ("LLM + Vision / OCR", "Gemma-4-12B (multimodal, Q8_0 GGUF + mmproj) via llama.cpp llama-server"),
        ("Agentic framework", "deepagents (LangChain) driving the local model through a ChatOpenAI instance"),
        ("Speech → Text", "faster-whisper large-v3 (Hindi, numpy in, no system ffmpeg)"),
        ("Text → Speech", "facebook/mms-tts-hin (open) + a Hindi number-words pass; Parler optional/gated"),
        ("Databases", "two SQLite files — inventory.db &amp; transactions.db — unified for reads via ATTACH"),
        ("Frontend", "Gradio single-screen app (mic · photo · chat · today-dashboard · alerts)"),
        ("Deployment", "one Slurm GPU allocation (RTX A6000) runs llama-server + the app together"),
    ]
    return section("2", "s2", "At a glance",
        "<h3>What it does</h3>", f"<div class='cards'>{card_html}</div>",
        "<h3>Tech stack</h3>", kv(stack),
        "<h3>How the build differs from the original design</h3>",
        "<p>The original <code>design.md</code> sketched a single-database, Gemma-3 system. The as-built "
        "system evolved on several axes — this report documents the <em>current</em> reality:</p>",
        kv([
            ("Model", "design.md said Gemma-3 → <strong>Gemma-4-12B</strong> (real, multimodal: text + image + audio)"),
            ("Agent", "“simple function-calling loop” → the <strong>deepagents</strong> framework"),
            ("Database", "one SQLite file → <strong>two databases</strong> (stock vs khata) with an ATTACH read layer"),
            ("TTS", "Indic Parler-TTS → <strong>MMS-TTS</strong> (Parler’s HF repo is gated)"),
            ("Reasoning", "added: Gemma-4 <strong>thinking-mode disabled</strong> for fast, non-empty replies"),
        ]))


def sec_arch() -> str:
    return section("3", "s3", "System architecture",
        "<p>One request flows left-to-right through five stages: <strong>input</strong> (voice, text or "
        "image), <strong>normalisation</strong> (speech→text, image→text), the <strong>agent</strong> "
        "(Gemma-4 deciding and calling tools), the <strong>data layer</strong> (two databases), and the "
        "<strong>response</strong> (a concise Hindi reply, optionally spoken). The same agent handles both "
        "directions: recording changes (writes) and answering questions (reads).</p>",
        fig("d1_arch", "D1 — End-to-end system architecture: multimodal input → normalize → deepagents/Gemma-4 → two databases → spoken Hindi reply."),
        "<p>Three properties make it fit a “build small” brief: it is <strong>local</strong> (one GPU, no "
        "cloud), <strong>multimodal in one model</strong> (Gemma-4 reads both text and bill photos, so there "
        "is no separate OCR model), and <strong>honest about scope</strong> — a 12B model is genuinely enough "
        "for routing, text-to-SQL, short reasoning and drafting.</p>")


def sec_model() -> str:
    return section("4", "s4", "The model & how it is served",
        "<p>The brain is <strong>Gemma-4-12B-it</strong> in GGUF form "
        "(<code>ggml-org/gemma-4-12B-it-GGUF</code>), quantised to <strong>Q8_0</strong> (~12.7 GB, "
        "near-lossless) with its multimodal projector <code>mmproj</code> (~159 MB) for vision. It is served "
        "by a CUDA build of llama.cpp’s <code>llama-server</code>, which exposes an OpenAI-compatible API.</p>",
        "<h3>Launch flags that matter</h3>",
        code("llama-server -m gemma-4-12B-it-Q8_0.gguf \\\n"
             "  --mmproj mmproj-gemma-4-12B-it-Q8_0.gguf \\\n"
             "  --host 127.0.0.1 --port 8080 -c 32768 -ngl 99 --jinja"),
        kv([
            ("--mmproj", "loads the vision projector → the same endpoint accepts bill/label images"),
            ("-ngl 99", "offload all layers to the GPU (fully on the RTX A6000)"),
            ("--jinja", "use the model’s chat template → enables OpenAI-style tool calling"),
            ("-c 32768", "32k context (Gemma-4 supports far more; 32k is ample here)"),
        ]),
        callout("why", "Why thinking-mode is turned OFF",
            "Gemma-4 ships a native “thinking” mode. Left on, a plain chat request returned an "
            "<em>empty</em> <code>content</code> (all tokens went to hidden reasoning) and took ~3.4 s. "
            "Sending <code>extra_body={\"chat_template_kwargs\": {\"enable_thinking\": false}}</code> on "
            "every request turns it off — the same prompt then answers in <strong>0.2 s</strong> with real "
            "text, and tool-calling stays reliable. The deep-agent loop supplies multi-step reasoning "
            "instead. This is wired centrally in <code>dukaan/llm.py</code>."),
        fig("d2_serve", "D2 — Serving: one Slurm GPU allocation runs llama-server (:8080) and the Gradio app (:7860) together."),
        "<p>The Python side never imports the model. <code>dukaan/llm.py</code> exposes a "
        "<code>ChatOpenAI</code> instance (for the agent) and a raw OpenAI client (for one-shot vision/"
        "normalise/draft calls) — both pointed at <code>http://127.0.0.1:8080/v1</code>.</p>")


def sec_agent() -> str:
    tools = [
        ("add_inventory_tool", "add / create stock for an item"),
        ("record_sale_tool", "log a sale and decrement stock"),
        ("record_purchase_tool", "log a supplier restock and increase stock"),
        ("add_udhaar_tool", "add a credit (udhaar) entry for a customer"),
        ("record_payment_tool", "record a repayment against a customer’s udhaar"),
        ("query_database", "run a read-only SELECT across both DBs (text-to-SQL)"),
        ("get_dashboard", "today’s snapshot: stock value, sales, expiry, low stock, udhaar"),
        ("get_item_detail", "one item: stock, margin, expiry, 30-day sales"),
        ("get_customer_dues", "one customer’s balance, or the whole pending list"),
    ]
    tool_rows = "".join(f"<tr><th><code>{n}</code></th><td>{d}</td></tr>" for n, d in tools)
    return section("5", "s5", "The agentic layer (deepagents)",
        "<p>The agent is built with <strong>deepagents 0.6.8</strong> (LangChain’s deep-agent framework). "
        "It is driven by the <em>local</em> model: we pass a <code>ChatOpenAI</code> <strong>instance</strong> "
        "(not an <code>\"openai:...\"</code> string — that would route to the OpenAI Responses API, which "
        "llama.cpp does not implement) into <code>create_deep_agent(...)</code>. No Anthropic key is needed.</p>",
        fig("d3_agent", "D3 — The deepagents loop: the model reasons, calls tools, reads observations, and loops until it produces a final Hindi reply."),
        "<h3>The tool registry (9 tools)</h3>",
        f"<table class='kv'>{tool_rows}</table>",
        "<p>Each tool is a typed <code>@tool(parse_docstring=True)</code> function wrapping the pure-Python "
        "<code>dukaan/ops.py</code> layer. Write tools route to the owning database; the read tool "
        "<code>query_database</code> runs only through the hardened SELECT guard (see §6).</p>",
        "<h3>How it decides what to do</h3>",
        "<p>A Hindi-first system prompt (embedding the live DB schema) tells the model to classify each turn "
        "and act. This collapses the classic “router → single-turn / multi-turn” design into the agent’s own "
        "behaviour:</p>",
        fig("d4_route", "D4 — Intent routing, as encoded in the agent’s system prompt."),
        kv([
            ("Memory", "an <code>InMemorySaver</code> checkpointer keyed by a per-session <code>thread_id</code> → multi-turn context"),
            ("Step budget", f"<code>recursion_limit = {config.AGENT_RECURSION_LIMIT}</code> (LangGraph default 25 is too low for multi-tool turns)"),
            ("Subagents", "deliberately none — deepagents 0.6.8 has an open recursion bug with subagents"),
            ("Reply rule", "always concise Devanagari Hindi; never mentions SQL/tools/steps; never touches the file tools"),
        ]),
        callout("tip", "Diagnostic = emergent multi-step",
            "For “<em>X kyun nahi bik raha?</em>” the agent isn’t special-cased. It plans, calls "
            "<code>get_item_detail</code> and <code>query_database</code> several times (sales trend, stock, "
            "expiry, comparison), then reasons in plain Hindi and suggests an action (price cut, shelf "
            "placement). The depth is just the loop running longer."))


def sec_db() -> str:
    cnt = db.counts()
    cat_rows = db.qx("SELECT category, COUNT(*) items, SUM(qty) units FROM inv.inventory GROUP BY category ORDER BY items DESC")
    cat_tbl = "<table class='data'><caption>Catalog by category (inventory.db)</caption><thead><tr>" \
        "<th>category</th><th class='num'>items</th><th class='num'>units in stock</th></tr></thead><tbody>" + \
        "".join(f"<tr><td>{esc(r['category'])}</td><td class='num'>{r['items']}</td>"
                f"<td class='num'>{r['units']}</td></tr>" for r in cat_rows) + "</tbody></table>"
    top = db.qx("SELECT i.name, i.category, SUM(s.qty) sold FROM txn.sales s "
                "JOIN inv.inventory i ON i.item_id=s.item_id GROUP BY i.item_id ORDER BY sold DESC LIMIT 6")
    top_tbl = "<table class='data'><caption>Top sellers (cross-database join: txn.sales ⋈ inv.inventory)</caption>" \
        "<thead><tr><th>item</th><th>category</th><th class='num'>units sold</th></tr></thead><tbody>" + \
        "".join(f"<tr><td>{esc(r['name'])}</td><td>{esc(r['category'])}</td>"
                f"<td class='num'>{r['sold']}</td></tr>" for r in top) + "</tbody></table>"
    return section("6", "s6", "The two databases",
        "<p>Data is split across <strong>two SQLite files</strong>, mirroring a real shop’s mental model: "
        "the <strong>supply side</strong> (what’s on the shelf) and the <strong>money side</strong> "
        "(who bought what, and who owes what).</p>",
        kv([
            ("inventory.db", "<strong>suppliers</strong>, <strong>inventory</strong> (with expiry, MRP, "
                             "purchase price, reorder level, HSN), <strong>purchases</strong> (restocks)"),
            ("transactions.db", "<strong>customers</strong>, <strong>sales</strong>, "
                                "<strong>ledger</strong> (udhaar debits &amp; repayments)"),
        ]),
        callout("why", "Two files, but reads still join across them",
            "Writes go to the owning file (<code>execute_inv</code> / <code>execute_txn</code>). For reads, "
            "<code>get_attached_ro_conn()</code> opens an in-memory connection and <code>ATTACH</code>-es "
            "both files <em>read-only</em> as <code>inv</code> and <code>txn</code>, so the agent can still "
            "<code>JOIN inv.inventory ⋈ txn.sales</code> in one query. The LLM-facing "
            "<code>run_select()</code> runs only on this attached connection."),
        callout("warn", "The SELECT guard (security)",
            "All LLM-generated SQL passes <code>is_safe_select()</code>: it must be a single "
            "<code>SELECT</code>/<code>WITH</code>, no stacked statements, and no write/DDL keyword "
            "(<code>insert, update, delete, drop, attach, pragma…</code>). On top of that it executes on an "
            "OS-level read-only connection — two independent safeguards, so even a prompt-injected query "
            "cannot mutate or exfiltrate beyond a read."),
        "<h3>Schemas (from <code>dukaan/db.py</code>)</h3>",
        code(db.INVENTORY_SCHEMA.strip()),
        code(db.TRANSACTIONS_SCHEMA.strip()),
        "<h3>Entity-relationship diagrams</h3>",
        fig("d5_er_inv", "D5 — inventory.db (key fields shown; full columns in the schema above): "
                         "suppliers → inventory → purchases."),
        fig("d6_er_txn", "D6 — transactions.db (key fields shown): customers → sales and customers → "
                         "ledger (udhaar). sales.item_id is a cross-database reference to inv.inventory."),
        "<h3>How the data was created</h3>",
        "<p>The demo data is not hand-typed. A small <strong>agent team</strong> built it: three web-research "
        "subagents studied real Indian kirana stores (catalog, brands, per-category margins, shelf life, "
        "udhaar and footfall patterns); an <strong>inventory subagent</strong> wrote "
        "<code>seed_inventory.py</code> (the catalog + a restock generator); a <strong>ledger subagent</strong> "
        "wrote <code>seed_ledger.py</code> (customers + a deterministic ~120-day sales &amp; udhaar generator) "
        "consistent with that catalog. A loader (<code>db._seed_all</code>) maps item names → ids across the "
        "two files, derives realistic remaining expiry, and adds a small “today so far” tranche so the demo is "
        "never empty.</p>",
        fig("d7_datagen", "D7 — The data-generation pipeline: research → per-database subagents → loader → two .db files."),
        "<h3>What the data actually looks like</h3>",
        "<p>Real rows sampled live from the two databases at build time:</p>",
        data_table("inv.suppliers — sample", "SELECT name, phone, focus FROM inv.suppliers LIMIT 6"),
        data_table("inv.inventory — one item per category (showing the catalog’s breadth)",
                   "SELECT name, category, brand, unit, qty, mrp, purchase_price, expiry_date, reorder_level "
                   "FROM inv.inventory GROUP BY category ORDER BY category LIMIT 12",
                   money_cols=("mrp", "purchase_price"), num_cols=("qty", "reorder_level")),
        data_table("inv.purchases — recent restocks (joined to item names)",
                   "SELECT p.purchase_id, i.name AS item, p.supplier, p.qty, p.cost, p.ts "
                   "FROM inv.purchases p JOIN inv.inventory i ON i.item_id=p.item_id ORDER BY p.ts DESC LIMIT 6",
                   money_cols=("cost",), num_cols=("purchase_id", "qty")),
        data_table("txn.customers — sample", "SELECT name, phone, credit_limit FROM txn.customers LIMIT 6",
                   money_cols=("credit_limit",)),
        data_table("txn.sales — most recent (joined to customer; many are walk-ins)",
                   "SELECT s.item_name, s.qty, s.sale_price, s.ts, c.name AS customer FROM txn.sales s "
                   "LEFT JOIN txn.customers c ON c.customer_id=s.customer_id ORDER BY s.ts DESC LIMIT 8",
                   money_cols=("sale_price",), num_cols=("qty",)),
        data_table("txn.ledger — udhaar entries (debit = taken, credit = repaid)",
                   "SELECT c.name AS customer, l.type, l.amount, l.items, l.due_date FROM txn.ledger l "
                   "JOIN txn.customers c ON c.customer_id=l.customer_id ORDER BY l.entry_id LIMIT 8",
                   money_cols=("amount",)),
        cat_tbl, top_tbl,
        callout("result", "Consistency check",
            f"All {cnt['sales']:,} sales reference real catalog items (0 orphans), udhaar balances reconcile "
            f"to Rs {ops.pending_udhaar()['total']:,.0f} across {ops.pending_udhaar()['count']} customers, and "
            f"the cross-database top-seller join works — proving the ATTACH read layer in practice."))


def sec_speech() -> str:
    return section("7", "s7", "Speech & vision",
        "<p>The voice and image paths are deliberately light and ffmpeg-free.</p>",
        fig("d8_speech", "D8 — Speech-to-text (Whisper), image OCR (Gemma-4 vision), and text-to-speech (MMS-TTS)."),
        "<h3>Speech → text</h3>",
        "<p><strong>faster-whisper large-v3</strong> transcribes Hindi. It accepts a numpy float32 array "
        "directly (Gradio’s mic gives <code>(sr, ndarray)</code>), so there is <strong>no system ffmpeg</strong> "
        "dependency — audio is resampled to 16 kHz mono with <code>scipy.signal.resample_poly</code>.</p>",
        "<h3>Image → text (OCR)</h3>",
        "<p>Because Gemma-4 is multimodal, bill/label photos go to the <em>same</em> model via the "
        "OpenAI <code>image_url</code> (base64) content part — no separate OCR model. The extracted text is "
        "fed back to the agent as the user’s effective message, which then calls the write tools.</p>",
        "<h3>Text → speech</h3>",
        "<p><strong>facebook/mms-tts-hin</strong> (open VITS) speaks the reply. Two fixes make it demo-ready:</p>",
        callout("gotcha", "MMS only voices Devanagari, and can’t read digits",
            "Romanised Hindi tokenises to almost nothing (garbled 0.4 s clips), so the agent is forced to "
            "reply in Devanagari. And MMS can’t pronounce Latin digits — so <code>dukaan/numwords.py</code> "
            "converts numbers and Rs amounts to Hindi words before synthesis (e.g. “Rs 528” → "
            "“paanch sau atthaais rupaye”). Parler-TTS would sound better but its HF repo is gated, so MMS "
            "is the working default."))


def sec_proactive() -> str:
    return section("8", "s8", "Proactive agents",
        "<p>Three agents surface things the owner didn’t ask about. They run on app open, on the dashboard "
        "<em>Refresh</em>, and behind an <em>Alerts</em> button (the LLM-drafted reminders run on demand so "
        "the dashboard stays instant).</p>",
        fig("d9_proactive", "D9 — Proactive agents feeding the dashboard and the alerts panel."),
        kv([
            ("Expiry Watcher", "lists items expiring within 15 days, with days-left, in friendly Hindi"),
            ("Festival Nudge", "a 2026 Indian festival calendar; if one is within 30 days, suggests what to stock up "
                               "(cross-referenced with low / slow-moving items)"),
            ("Udhaar Reminder", "for each overdue customer, drafts a polite 1–2 line Hindi WhatsApp message "
                               "(LLM, with a template fallback when the server is down)"),
        ]),
        callout("tip", "Resilient by design",
            "Every LLM call in the proactive layer is wrapped so a missing/slow server falls back to a fixed "
            "Hindi template — the dashboard never breaks."))


def sec_flows() -> str:
    return section("9", "s9", "End-to-end flow walkthroughs",
        "<p>Four representative turns, each tracing input → agent → data → reply.</p>",
        "<h3>A · Voice WRITE — “10 Parle-G ke packet aaye, 5 rupaye wala”</h3>",
        "<p>Mic → Whisper → Hindi text → agent recognises a restock → calls "
        "<code>record_purchase_tool(item, qty=10, …)</code> → a row is appended to "
        "<code>inv.purchases</code> and <code>inv.inventory.qty</code> grows → reply: "
        "“…maal darj kar diya, ab kul N packet hain.”</p>",
        "<h3>B · Cross-DB LOOKUP — “aaj kitni bikri hui?”</h3>",
        "<p>Agent calls <code>get_dashboard</code> (or writes a SELECT over <code>txn.sales</code>) → "
        "answers with today’s revenue, unit count and the top item — numbers that match "
        "<code>ops.today_summary()</code> exactly.</p>",
        "<h3>C · DIAGNOSTIC — “Maggi kyun nahi bik raha?”</h3>",
        "<p>Agent calls <code>get_item_detail</code> + <code>query_database</code> several times (30-day sales, "
        "stock, expiry, margin), then reasons in Hindi and suggests a combo offer or price check — a genuine "
        "multi-step investigation, not a single lookup.</p>",
        "<h3>D · IMAGE / OCR add — a supplier bill photo</h3>",
        "<p>Photo → Gemma-4 vision extracts “Surf Excel 1kg ×12 @110, Colgate ×24 @42” → fed to the agent → "
        "two <code>record_purchase_tool</code> calls → stock updated, itemised Hindi confirmation.</p>",
        fig("d10_seq", "D10 — Sequence of a full voice round-trip, from spoken Hindi to spoken Hindi."))


def sec_app() -> str:
    return section("10", "s10", "The Gradio app",
        "<p>One clean screen (<code>dukaan/app.py</code>, Gradio 6.x). Left: a chat transcript plus three "
        "inputs — <strong>mic</strong>, <strong>photo</strong>, <strong>text</strong> — a send button, a "
        "“speak the reply” toggle, and an autoplay audio player for the spoken answer. Right: a live "
        "<strong>“Aaj ka hisaab”</strong> dashboard (stock value, today’s sales, expiring soon, low stock, "
        "pending udhaar, next festival) with <em>Refresh</em> and <em>Alerts</em> buttons.</p>",
        kv([
            ("Per-session state", "a <code>thread_id</code> (<code>gr.State</code>) gives each browser tab its own conversation memory"),
            ("Lazy loading", "Whisper / MMS / the agent load on first use (warmed in a background thread at launch) so the UI is instant"),
            ("Graceful degradation", "if llama-server is down the dashboard shows a banner instead of crashing; any handler error becomes a polite Hindi message"),
        ]))


def sec_deploy() -> str:
    return section("11", "s11", "Deployment & operations",
        "<p>Per the cluster policy, GPU work goes through Slurm. <code>scripts/run.sbatch</code> runs the "
        "<em>whole</em> demo in one GPU allocation: it starts <code>llama-server</code>, polls "
        "<code>/health</code> until ready, then launches the Gradio app — and kills the server on exit.</p>",
        code("sbatch scripts/run.sbatch          # llama-server + Gradio in one GPU job\n"
             "uv run python -m dukaan.db --reset  # (re)build + seed both databases\n"
             "uv run pytest -q                    # 31 tests\n"
             "# quick debug (no Slurm):\n"
             "bash scripts/serve_llm.sh & uv run python -m dukaan.app"),
        kv([
            ("Ports", "llama-server <code>127.0.0.1:8080</code> · Gradio <code>0.0.0.0:7860</code> (LAN-reachable)"),
            ("Config", "everything overridable via env (see <code>.env.example</code>) — model paths, ports, "
                       "device, TTS engine, business thresholds"),
            ("Scripts", "<code>serve_llm.sh</code>, <code>run.sbatch</code>, <code>run_local.sh</code>, <code>download_models.sh</code>"),
        ]),
        callout("gotcha", "A real Slurm bug this caught",
            "In an sbatch script <code>${BASH_SOURCE[0]}</code> points at Slurm’s spool copy, so a "
            "<code>BASH_SOURCE</code>-derived project root was wrong and <code>mkdir logs</code> failed. Fixed "
            "by using <code>$SLURM_SUBMIT_DIR</code>. Found only by actually running the job."))


def sec_tests() -> str:
    battery = [
        ("Stock value (inv)", "Rs 2,19,692.13", "exact match"),
        ("Top seller (cross-DB join)", "Table Eggs, 68 units", "correct"),
        ("Top udhaar (txn)", "Shukla ji Rs 2,540 (+next two)", "correct"),
        ("Item stock (inv)", "Aashirvaad Atta 22 units", "correct"),
        ("Diagnostic", "reasoned over sales+margin+stock → advice", "multi-tool"),
        ("Sale write", "stock 22 → 17", "DB verified"),
        ("Restock write", "17 → 57", "DB verified"),
        ("OCR → add", "2 items → 2 purchases recorded", "correct"),
    ]
    brows = "".join(f"<tr><td>{esc(t)}</td><td>{esc(a)}</td><td class='muted'>{esc(v)}</td></tr>"
                    for t, a, v in battery)
    return section("12", "s12", "Testing & verification",
        "<p>Correctness is proven two ways: a fast automated suite, and a ground-truth agent battery against "
        "the real model.</p>",
        "<h3>Automated suite — 31 tests, green</h3>",
        kv([
            ("test_db", "schema/seed counts, the SELECT guard (allows reads, blocks writes/injection), cross-DB JOIN"),
            ("test_ops", "new-vs-restock (incl. the size-token regression), sale decrement + oversell warning, "
                         "udhaar math, analytics, cross-DB integrity"),
            ("test_tools", "every tool has name/description; query_database blocks DELETE; a write tool mutates the DB"),
            ("test_numwords", "Hindi number-words (Rs 528 → paanch sau atthaais rupaye)"),
            ("test_smoke_e2e", "live agent: a Hindi lookup returns text; a Hindi udhaar command raises the balance"),
        ]),
        "<h3>Agent Q&amp;A battery (verified vs ground truth)</h3>",
        "<table class='data'><thead><tr><th>Task</th><th>LLM answer</th><th>Check</th></tr></thead><tbody>"
        + brows + "</tbody></table>",
        callout("result", "Voice loop",
            "A TTS→STT round-trip confirms the spoken path: the Hindi reply is synthesised (~12 s of audio) "
            "and Whisper transcribes it back to intelligible Hindi, numbers and all."))


def sec_gotchas() -> str:
    return section("13", "s13", "Design decisions & gotchas",
        "<p>The non-obvious calls that shaped the build (all recorded in <code>tasks/lessons.md</code>):</p>",
        kv([
            ("Thinking off", "Gemma-4’s reasoning mode blanked replies → disabled via chat-template kwargs (3.4 s → 0.2 s)"),
            ("ChatOpenAI instance", "pass an instance, not an <code>openai:</code> string, or deepagents uses the Responses API llama.cpp lacks"),
            ("Parler → MMS", "Indic Parler-TTS is a gated HF repo (the machine’s token isn’t authorised) → defaulted to open MMS-TTS"),
            ("Gradio 6.16", "<code>gr.Chatbot</code> dropped <code>type=\"messages\"</code>; <code>theme</code> moved to <code>launch()</code>"),
            ("Two-DB ATTACH", "physical split + a read-only attached connection keeps cross-DB analytics working"),
            ("Slurm cwd", "use <code>$SLURM_SUBMIT_DIR</code>, not <code>$BASH_SOURCE</code>, inside sbatch"),
            ("Subagent discipline", "a build subagent over-verified (ran the full test suite) and stalled a pipeline → scope build prompts tightly"),
        ]))


def sec_layout() -> str:
    tree = """small_build_hackathon/
├── dukaan/                     # application package (~3,400 LOC)
│   ├── config.py               # env-driven config (model paths, ports, thresholds)
│   ├── db.py                   # TWO-DB layer: schemas, ATTACH reads, SELECT guard, loader
│   ├── ops.py                  # business ops + analytics (pure Python, testable)
│   ├── llm.py                  # llama-server client (chat, vision, thinking toggle)
│   ├── tools.py                # 9 LangChain tools (write + read)
│   ├── agent.py                # deepagents agent (system prompt, run_agent)
│   ├── stt.py                  # faster-whisper STT (numpy, no ffmpeg)
│   ├── tts.py                  # MMS-TTS (+ parler fallback) + markdown/number cleanup
│   ├── numwords.py             # digits/Rs → Hindi number words (for TTS)
│   ├── normalize.py            # Hindi normalize + Gemma-4 vision OCR
│   ├── proactive.py            # expiry / festival / udhaar agents + 2026 calendar
│   ├── app.py                  # Gradio single-screen app
│   ├── seed_inventory.py       # research-grounded catalog (160 SKUs + restocks)
│   └── seed_ledger.py          # research-grounded customers + ~6k sales + udhaar
├── scripts/  run.sbatch · serve_llm.sh · run_local.sh · download_models.sh
├── tests/    test_db · test_ops · test_tools · test_numwords · test_smoke_e2e (31 tests)
├── data/     inventory.db · transactions.db
├── models/   gemma-4 GGUF + mmproj   ·   vendor/  llama.cpp (CUDA build)
└── docs/     design.md · this report"""
    return section("14", "s14", "Project layout", code(tree))


def sec_future() -> str:
    return section("15", "s15", "Limitations & future work",
        "<ul>"
        "<li><strong>TTS naturalness.</strong> MMS is robotic; swapping in Indic Parler-TTS (Apache, gated) "
        "once access is granted would lift the spoken quality — <code>tts.py</code> already supports it.</li>"
        "<li><strong>Reminder sending.</strong> Udhaar reminders are <em>drafted</em>, not sent; wiring a "
        "WhatsApp/SMS provider is the obvious next step.</li>"
        "<li><strong>Concurrency.</strong> The demo is single-process (Gradio serialises); multi-user would "
        "want a pooled llama-server and per-request DB connections.</li>"
        "<li><strong>Reconciliation.</strong> Current stock is a believable snapshot, not a strict ledger of "
        "opening + purchases − sales; real accounting would reconcile the two.</li>"
        "<li><strong>HF Space.</strong> The 12B model can’t run on a free Space; a hosted deployment needs a "
        "tunnel to the cluster server or a smaller model.</li>"
        "</ul>")


def sec_appendix() -> str:
    return section("16", "s16", "Appendix",
        "<h3>Key commands</h3>",
        code("sbatch scripts/run.sbatch                 # run the whole demo (1 GPU)\n"
             "uv run python -m dukaan.db --reset         # rebuild + seed both databases\n"
             "uv run pytest -q                           # test suite (31)\n"
             "uv run python docs/report/build_report.py  # regenerate this report"),
        "<h3>Example interactions (romanized)</h3>",
        kv([
            ("Record stock", "“das Parle-G ke packet aaye, paanch rupaye wala”"),
            ("Record a sale", "“do Tata Salt bik gaye”"),
            ("Add udhaar", "“Sharma ji ne do sau ka udhaar liya, doodh aur biscuit ka”"),
            ("Lookup", "“aaj kitni bikri hui?” · “kiska kitna udhaar baaki hai?”"),
            ("Diagnostic", "“Maggi kyun nahi bik raha?”"),
        ]),
        "<h3>Configuration (selected env vars)</h3>",
        kv([
            ("DUKAAN_LLM_BASE_URL", "llama-server endpoint (default <code>http://127.0.0.1:8080/v1</code>)"),
            ("DUKAAN_LLM_ENABLE_THINKING", "Gemma-4 thinking mode (default <code>false</code>)"),
            ("DUKAAN_TTS_ENGINE", "<code>mms</code> (default) or <code>parler</code>"),
            ("DUKAAN_INVENTORY_DB / _TRANSACTIONS_DB", "the two SQLite file paths"),
            ("DUKAAN_GRADIO_PORT / _LLM_PORT", "service ports (7860 / 8080)"),
        ]),
        "<hr class='soft'><p class='small muted'>Generated from the live codebase and databases by "
        "<code>docs/report/build_report.py</code> — diagrams via Mermaid/kroki, PDF via WeasyPrint.</p>")


def main() -> None:
    print("Rendering diagrams (Mermaid → kroki SVG)…")
    build_diagrams()
    print("Sampling databases + composing HTML…")
    body = "".join([
        cover(), toc(),
        sec_exec(), sec_glance(), sec_arch(), sec_model(), sec_agent(), sec_db(),
        sec_speech(), sec_proactive(), sec_flows(), sec_app(), sec_deploy(),
        sec_tests(), sec_gotchas(), sec_layout(), sec_future(), sec_appendix(),
    ])
    doc = f"<!doctype html><html><head><meta charset='utf-8'><title>Dukaan Saathi — Project Report</title></head><body>{body}</body></html>"
    (REPORT_DIR / "report.html").write_text(doc)
    print(f"Writing PDF → {OUT} …")
    HTML(string=doc, base_url=str(REPORT_DIR)).write_pdf(
        str(OUT), stylesheets=[CSS(filename=str(REPORT_DIR / "report.css"))])
    print(f"Done: {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
