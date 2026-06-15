# Dukaan Saathi: System Architecture

> **Build Small Hackathon · Backyard AI track**
> A Hindi-first, voice-driven inventory + *udhaar* (credit) ledger assistant for a small kirana shop owner.
> *Small enough to run cheaply, big enough to change a shopkeeper's day.*

---

## 1. The person & the problem

**Persona:** A local kirana / general-store owner (e.g. *Ramesh bhaiya* down the street). Runs the shop solo, tracks stock and customer credit in a paper *bahi-khata*, is comfortable speaking Hindi but not typing English or using spreadsheets.

**Pain points it fixes:**
- Stock and *udhaar* live in a paper notebook, easy to lose, impossible to search.
- No idea what's about to **expire** or **isn't selling**.
- Forgets to **stock up before festivals** (demand spikes go unmet).
- Awkward / forgets to **chase customers for pending credit**.

**Interaction model:** He just **talks to the app in Hindi** (or snaps a photo of a bill / shows a label). Everything else is automatic.

---

## 2. High-level architecture

```mermaid
flowchart TB
    subgraph IN["Multimodal Input (Hindi-first)"]
        V["Voice (Hindi)"]
        T["Text (Hindi / Hinglish)"]
        I["Image (bill / label / invoice)"]
    end

    subgraph PRE["Normalization Layer"]
        STT["Whisper<br/>(Speech → Text)"]
        OCR["Surya OCR +<br/>Gemma 4 vision<br/>(image → text)"]
        NORM["Gemma 4<br/>(normalize + translate<br/>Hindi → structured EN)"]
    end

    ROUTER{{"Router Agent<br/>(Gemma 4)<br/>Read or Write?"}}

    subgraph WRITE["Ingest / Write Pipeline"]
        WTOOL["Agent → call tool"]
        DBW["Update DB"]
    end

    subgraph READ["Query / Read Pipeline"]
        ST["Single-Turn<br/>→ text-to-SQL → run"]
        MT["Multi-Turn<br/>→ analyse → get result"]
        SUM["Gemma 4<br/>Summarize result"]
    end

    DB[("SQLite<br/>inventory · sales · purchases<br/>ledger · customers")]
    TTS["Veena TTS → Hindi voice"]

    V --> STT --> NORM
    T --> NORM
    I --> OCR --> NORM
    NORM --> ROUTER
    ROUTER -->|"write intent"| WTOOL --> DBW --> DB
    ROUTER -->|"read intent"| ST
    ROUTER -->|"read intent"| MT
    ST <--> DB
    MT <--> DB
    ST --> SUM
    MT --> SUM
    SUM --> TTS
    DBW --> SUM
```

---

## 3. Write / Ingest flow  *(from whiteboard 2)*

Adding stock, recording a sale/purchase, or noting credit, by photo, text, or voice.

```mermaid
flowchart LR
    I["Image"] --> OCR["OCR<br/>(Surya + Gemma 4)"]
    T["Text"] --> AG
    Vo["Voice"] --> STT["STT<br/>(Whisper)"]
    OCR --> AG{{"Agent<br/>decide tool<br/>based on output"}}
    STT --> AG
    AG --> TOOL["Tool call"]
    TOOL --> DB[("Update DB")]

    TOOL -. "examples" .-> EX["add_inventory()<br/>record_sale()<br/>record_purchase()<br/>add_udhaar() / record_payment()"]
```

**Example:** *"10 Parle-G ke packet aaye, 5 rupaye wala, 100 piece"* → Whisper → Gemma extracts `{item: Parle-G, qty: 100, mrp: 5}` → agent calls `add_inventory()` → row written.

---

## 4. Read / Query flow  *(from whiteboard 1)*

```mermaid
flowchart LR
    T["Text (Hindi)"] --> EN
    Vo["Voice (Hindi)"] --> STT["Whisper STT"] --> EN["Gemma:<br/>→ EN text"]
    EN --> Q["User Query Task<br/>(retrieve / agent-call / route)"]
    Q --> R{{"Router"}}

    R -->|"simple lookup"| S["Single-Turn"]
    R -->|"reasoning / diagnostic"| M["Multi-Turn"]

    S --> SQL["Generate SQL query,<br/>run, get result"]
    M --> AN["Analyse &<br/>get result<br/>(multi-step loop)"]

    SQL --> RES["Result → LLM"]
    AN --> RES
    RES --> SUMM["Summarize<br/>(Gemma)"]
    SUMM --> TTS["TTS (Hindi)"]
```

- **Single-Turn** → one shot text-to-SQL on SQLite (e.g. *"aaj kitni biri hui?"* → `SELECT SUM(...) FROM sales WHERE date=today`).
- **Multi-Turn** → agentic loop that queries, reasons, re-queries (e.g. *"Maggi kyun nahi bik raha?"* → pulls sales trend, compares to last month, checks stock & expiry, reasons).

---

## 5. Router decision logic

```mermaid
flowchart TD
    Q["Normalized query"] --> C{Intent?}
    C -->|"add / record / note<br/>(stock, sale, credit)"| W["WRITE path → tool call → DB"]
    C -->|"ask / show / how much / why"| RD{Complexity?}
    RD -->|"single fact / lookup"| ST["SINGLE-TURN<br/>text-to-SQL"]
    RD -->|"reasoning / multi-step /<br/>diagnostic"| MT["MULTI-TURN<br/>analyse loop"]
    C -->|"reminder / nudge"| PR["PROACTIVE tool<br/>(draft msg / alert)"]
```

---

## 6. Data model (SQLite, local, zero-infra)

```mermaid
erDiagram
    INVENTORY ||--o{ SALES : "sold as"
    INVENTORY ||--o{ PURCHASES : "restocked by"
    CUSTOMERS ||--o{ LEDGER : "owes / pays"
    CUSTOMERS ||--o{ SALES : "buys"

    INVENTORY {
        int item_id PK
        string name
        string category
        int qty
        float mrp
        float purchase_price
        date expiry_date
    }
    SALES {
        int sale_id PK
        int item_id FK
        int qty
        float sale_price
        datetime ts
        int customer_id FK
    }
    PURCHASES {
        int purchase_id PK
        int item_id FK
        string supplier
        int qty
        float cost
        datetime ts
    }
    LEDGER {
        int entry_id PK
        int customer_id FK
        string type
        float amount
        string items
        date due_date
        datetime ts
    }
    CUSTOMERS {
        int customer_id PK
        string name
        string phone
    }
```

> **Inventory = Selling + Purchase + MRP**, the three dimensions per item (`sale_price` via SALES, `purchase_price`/`cost` via PURCHASES, `mrp` on INVENTORY) so margin and stock value are always computable.

---

## 7. Proactive / scheduled agents

These run on a timer (or on app open), the assistant reaches out instead of waiting to be asked.

```mermaid
flowchart LR
    CRON["Scheduler<br/>(on open / daily)"] --> EXP["Expiry Watcher"]
    CRON --> FEST["Festival Nudge Agent"]
    CRON --> UDH["Udhaar Reminder Agent"]

    EXP -->|"items < N days to expiry"| ALERT["'Ye 5 item expire hone wale hain'"]
    FEST -->|"festival calendar + sales history"| STOCK["'Diwali aa rahi, X-Y stock badha lo'"]
    UDH -->|"overdue credit"| DRAFT["Drafts polite reminder msg"]

    DB[("SQLite")] --> EXP
    DB --> FEST
    DB --> UDH
```

---

## 8. Feature → component mapping

| Feature | What it does | Components used |
|---|---|---|
| **Voice Credit Ledger** (*udhaar khata*) | Add/retrieve credit entries by voice, "Sharma ji ne 200 ka udhaar liya" / "kiska kitna baaki hai?" | Whisper → Gemma → `add_udhaar` / `record_payment` / query LEDGER |
| **Inventory + Expiry** | Track stock; flag items nearing expiry | INVENTORY table + Expiry Watcher agent |
| **Festival-aware stock-up nudge** | Reminds to restock before demand spikes | Festival calendar + sales history + Nudge agent |
| **"Why not selling?" diagnostic** | Reasons about slow movers | Multi-turn loop over SALES trends + stock + expiry |
| **Reminder-drafter** (*udhaar ke paise*) | Drafts a polite Hindi collection message | LEDGER overdue + Gemma drafting → WhatsApp/SMS text |
| **Selling / Purchase / MRP** | Margin & stock-value visibility per item | SALES + PURCHASES + INVENTORY.mrp |

---

## 9. Tech stack & small-model fit

| Layer | Choice | Why it fits "build small" |
|---|---|---|
| **Speech → Text** | **faster-whisper** large-v3 | Robust Hindi STT, no proprietary API |
| **LLM (route, text-to-SQL, summarize, draft)** | **Gemma 4** (12B), Q4_K_M GGUF, via llama.cpp | Open-weight, vision-capable, strong instruction following, well under 32B |
| **OCR / image understanding** | **Surya** OCR pre-pass + **Gemma 4** vision | Surya reads the page first, Gemma makes sense of messy or handwritten bills |
| **TTS (speak back)** | **Veena** (Hindi / Hinglish) + **SNAC** decoder | One steady Hindi voice for the reply |
| **Database** | **SQLite** (inventory + transactions) | Two files, zero infra, read together via `ATTACH` |
| **Agent / tools** | **deepagents** (LangChain) loop | Read + write + vision tools, confirm-before-write |
| **Frontend** | **Gradio** "Bahi-Khata" on a **HF Space** | Custom HTML/CSS/JS ledger UI; GPU work hosted on **Modal** |

**Honest constraint fit:** the whole stack (faster-whisper + Surya + Gemma 4 12B with vision + Veena + SQLite + Gradio) is open-weight and modest. For the demo it is self-hosted on Modal across two warm L4 GPUs, one for the LLM and vision, one for speech. Because the models are open, the same setup can run on a shop's own hardware. No giant model is doing anything a small one can't.

---

## 10. Judging-criteria alignment

- **Specific & real problem** → one named kirana owner, paper-ledger pain, Hindi voice barrier.
- **Person actually used it** → voice-first + photo input means he can use it with zero typing; demo by recording him adding stock and asking "kiska udhaar baaki hai?"
- **Honest small-model fit** → Gemma + Whisper chosen *because* small models are enough here, not despite it.
- **Gradio polish** → single clean screen: mic button, photo upload, chat/answer area, and a "today" dashboard (stock value, expiring soon, udhaar pending).
