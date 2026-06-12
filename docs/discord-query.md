# Discord query for the Build Small organizers

Copy-paste this into the hackathon Discord (#help / #questions channel). Concise + polite.

---

Hi team! A few questions about my submission (a Gradio Space in the `build-small-hackathon` org) 🙏

1. **GPU for org Spaces:** When I set my Space (inside the org) to a paid GPU (T4), it returns `402 Payment Required`. How do participants get GPU for their submission Space — is there a grant/process, or is the expectation that the Space runs on free CPU and the **demo video** shows the full GPU run?

2. **External model hosting:** Is it OK for the models to run on an external GPU (e.g. **Modal**, which is a sponsor track) with the Gradio Space calling them over HTTP — or must the models run *inside* the Space itself?

3. **"Off the Grid" badge:** Does this badge require models to run locally / inside the Space, or does **self-hosting open-weight models on my own external GPU** (no proprietary cloud AI API) still qualify?

4. **"Tiny Titan" (≤4B):** Is the ≤4B limit measured on a model's **total** parameters or its **active** parameters (for MoE models)?

Thanks! 🙏

---

## My current best understanding (pending their confirmation)
- **Q2 (external hosting):** very likely **allowed** — Modal is literally a sponsor track ("use Modal for the runtime of your app"), which only makes sense if external inference counts. The required artifact is the *Gradio Space*; where the models run isn't restricted in the field guide.
- **Q3 (Off the Grid):** ambiguous. The badge text is "no cloud APIs; runs locally." Self-hosting open weights on Modal = no proprietary AI *API*, but it's not "local." So Modal hosting **may forfeit Off-the-Grid** — an acceptable trade for the Modal track. Confirm.
- **Q4 (Tiny Titan):** ambiguous; matters only for MoE. Our MiniCPM-V 4.6 variant is **dense ~3–4B** (total ≈ active), so it's safe either way — but worth pinning down.
- **Q1 (GPU):** the most practically important — the field guide already says the **video is the canonical eval if a live run is GPU-limited**, so a free-CPU Space + a GPU-filmed video is a valid submission regardless.
