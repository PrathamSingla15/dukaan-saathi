<!-- DRAFT — Field Notes blog for the Build Small Hackathon. Review before publishing to the HF blog.
     Fill the _bracketed_ bits (owner's first name, real quote, links) after the demo shoot. -->

# Dukaan Saathi: a Hindi-first AI that keeps a kirana's books — built small, on purpose

## The moment

It's 8pm at a kirana (corner store). A regular — call him _Rakesh_ — picks up atta, oil and a packet of Parle-G and says *"likh dena, kal de dunga"* (put it on my tab, I'll pay tomorrow). The owner nods, wipes his hands, opens a fat cloth-bound **bahi-khata**, finds the page with _Rakesh (Gali No. 4)_, and writes a line. Twice a week he flips through the whole book asking himself *"kiska kitna baaki hai?"* (who owes how much?). The stock works the same way: in his head, until something expires on the shelf.

That's the problem. Not a lack of apps — there are hundreds — but that **every one of them asks a busy, Hindi-speaking shopkeeper to type English into forms.** Dukaan Saathi asks him to do the one thing he already does all day: **talk.**

## What it does (and what it deliberately doesn't)

The owner **speaks in Hindi/Hinglish, or snaps a photo** of a supplier bill or his handwritten khata. The assistant figures out the intent and the entry writes itself:

- *"Rakesh ne 200 ka udhaar liya"* → a credit entry, staged for a **haan/nahi** confirm before anything is written.
- *"kiska kitna baaki hai?"* → the **net** balance per customer (debits minus repayments), ranked.
- 📸 a challan photo → line items read, matched to existing stock (restock vs. new), expiry estimated, ready to add in one tap.
- *"Parle-G kyun nahi bik raha?"* → a multi-step look across sales trend, stock and price — then a plain-Hindi answer and one suggestion.
- A morning briefing, expiry alerts, and a **festival stock-up nudge** driven by a verified 2026–2030 Indian festival calendar.

What it **doesn't** do is just as deliberate: it never auto-sends a WhatsApp message (it *drafts* a polite reminder you choose to send), it never writes to the books without a yes/no, and it never blocks a sale silently — it hard-stops an oversell and tells you why. Scope is a feature.

## The model journey: why "small" was a choice, not a compromise

The brain is **Gemma-4-12B** (a vision-capable GGUF on `llama.cpp`), driving a **deepagents** loop over ten tools, with **faster-whisper** for Hindi speech and **Veena** for Hindi/Hinglish voice. Every model is open-weight and ≤32B — the whole stack runs on **one 24 GB GPU on Modal**, no proprietary AI API anywhere.

We tried the alternatives. Here's the honest trade table:

| Option | The pull | Why we didn't ship it as the flagship |
|---|---|---|
| A frontier cloud API | best OCR + reasoning | It's a metered, proprietary API. A kirana shouldn't depend on a per-call meter, and it forfeits the whole "small, ownable, on-your-own-GPU" thesis. |
| **Gemma-4-12B (Q4–Q6 GGUF)** | vision OCR **and** reliable Hindi tool-calls, fits a single L4 | **Our flagship.** Big enough to read messy handwritten khata and form correct multi-tool calls; small enough to self-host cheaply. |
| MiniCPM-V 4.6 (≤4B) | even smaller — Tiny-Titan class | Genuinely strong; we ship it as a **companion ≤4B entry**. On the hardest input — handwritten Hindi khata OCR — it still trails the 12B. |
| Sub-2B models | cheapest of all | Tool-calling and Devanagari OCR degrade past usefulness for *this* job. |

The lesson the hackathon's name already hints at: **small + focused beats small + general.** A 12B model that does *only* one shopkeeper's books — with confirm-before-write, FEFO expiry, and a ledger-shaped UI — is more useful to Rakesh than a giant general model behind a paywall.

## Does it actually work? (numbers, not vibes)

- **38 hard end-to-end checks** with the real model over a seeded kirana's books (`scripts/e2e_full.py`): tool routing (read vs. write), ground-truth lookups, confirm-before-write, FEFO sale, qty-merge restock, **oversell hard-block**, SQL guard, ambiguity clarification ("kaun sa dūdh?"), vision challan receive, Hindi STT, TTS, morning briefing, festival calendar.
- **56 headless tests pass** (`uv run pytest -q`) on cross-DB integrity, FEFO lots, balances, staging and tools.
- The agent's reasoning is **visible**: every reply shows a small "how Saathi answered" trace of the tools it actually used.

## What's rough (honest limitations)

- We run a **quantized** Gemma (Q4–Q6) on the L4; on a bad day it can mis-form a tool call and apologize — a retry is fast, and Q6 buys headroom.
- Voice is **Hindi-first today** (the reply language is a deliberate single-locale choice; the multilingual seam exists but is off).
- The live demo rides **one Modal GPU** — we keep it warm and pre-warm it before a run; the demo video is the canonical eval.

## The custom UI

The screen *is* the account book it replaces — cream ledger paper, a red margin rule, brass numerals, with an instant English⇄हिंदी flip. It's hand-built HTML/CSS over Gradio, because a shopkeeper's tool should feel like *his* register, not a dashboard.

## Try it

- 🏪 Space: _<add link>_
- 🎬 Demo (Rakesh's real shop): _<add link>_
- 🧑‍💻 Code: _<add repo link>_

_Built for the Build Small Hackathon · Backyard AI. Small enough to run cheaply, big enough to change a shopkeeper's day._
