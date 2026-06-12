<!-- DRAFT social posts for the Build Small Hackathon. Pick one, attach the demo clip,
     fill the _bracketed_ bits, and link it from the Space README. -->

# Social post drafts

## X / Twitter (short)

> A kirana owner near me runs his whole shop from a cloth-bound ledger — credit, stock, expiry, all in his head.
>
> So I built **Dukaan Saathi**: he just *talks in Hindi* (or snaps a bill photo) and the books write themselves. 🏪🗣️
>
> All open models, ≤32B, on one GPU. No app to learn.
>
> 🎬 _<demo link>_ · 🏪 _<Space link>_
> @huggingface @gradio @modal #BuildSmall

## X / Twitter (thread, optional)

> 1/ Meet Dukaan Saathi — a Hindi-first AI that keeps a kirana (corner shop)'s books. The owner speaks; the udhaar (credit) ledger, stock and expiry update themselves. Built for #BuildSmall (Backyard AI). 🧵
>
> 2/ *"Rakesh ne 200 ka udhaar liya"* → staged entry → haan/nahi → done. *"kiska kitna baaki hai?"* → net balance per customer. 📸 a supplier bill → line items read, matched to stock, expiry estimated, added in one tap.
>
> 3/ Small on purpose: Gemma-4-12B (vision) + Whisper (Hindi) + Veena (Hinglish voice), all open-weight, ≤32B, on one Modal L4. No proprietary API. A ≤4B MiniCPM-V build runs the same app.
>
> 4/ It's safe on camera: confirm-before-write, an oversell hard-block, and a visible "how Saathi answered" trace so you see the agent reasoning over the books.
>
> 5/ The screen *is* the bahi-khata it replaces — cream paper, brass numerals, instant EN⇄हिं.
> 🎬 _<demo>_ · 🏪 _<Space>_ · 📓 _<blog>_
> @huggingface @gradio @modal

## LinkedIn (longer)

> **What if a shopkeeper never had to type?**
>
> Near me, a kirana (corner store) owner runs everything from a cloth-bound ledger — who owes what, what's in stock, what's about to expire — mostly in his head and in Hindi. Every grocery app I've seen asks him to type English into forms. So none of them stuck.
>
> For the **Build Small Hackathon**, I built **Dukaan Saathi** — a Hindi-first assistant where he just *talks* (or snaps a photo of a supplier bill or his handwritten khata), and the books keep themselves: voice credit ledger, photo-bill receiving, FEFO expiry, festival stock-up nudges, polite payment-reminder drafts.
>
> The interesting constraint was **"build small."** Instead of a frontier cloud API, the whole stack is open-weight models ≤32B — Gemma-4-12B (vision) + faster-whisper + Veena TTS — on a single GPU hosted on Modal. Big enough to read messy handwritten Hindi and plan multi-step actions; small enough that a shop could own it. There's also a ≤4B MiniCPM-V build of the exact same app.
>
> My takeaway: **small + focused beats small + general.** A model that does only one shopkeeper's books — with confirm-before-write and a UI shaped like his actual register — is worth more to him than a giant model behind a paywall.
>
> 🎬 Demo (a real shop): _<link>_  ·  🏪 Try it: _<link>_  ·  📓 Write-up: _<link>_
>
> Built with @Hugging Face, @Gradio and @Modal. #BuildSmall #AI #Bharat #SmallModels
