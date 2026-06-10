"""Evaluate ai4bharat/indic-parler-tts on Hinglish / Devanagari / English text.

Generates one WAV per sample into ``logs/`` so the quality (esp. Hinglish, which
the current MMS-Hindi model cannot speak) can be judged by ear. Run inside the
isolated ``parler-eval`` venv, on a GPU (via Slurm). Loads nothing from the
dukaan package — standalone on purpose.
"""

from __future__ import annotations

import time
from pathlib import Path

import soundfile as sf
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

REPO = "ai4bharat/indic-parler-tts"
OUT = Path("/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/logs")
OUT.mkdir(parents=True, exist_ok=True)

# App-relevant voice descriptions (indic-parler-tts is steered by a text prompt;
# "Rohit"/"Divya" are recommended Hindi speakers for a consistent voice).
DESC_M = ("Rohit speaks in a clear, warm and friendly voice at a natural pace, "
          "with very clean audio and no background noise.")
DESC_F = ("Divya speaks in a clear, warm and friendly voice at a natural pace, "
          "with very clean audio and no background noise.")

# (slug, description, text) — Hinglish is the point; a Devanagari and an English
# line are included as references.
SAMPLES = [
    ("01_confirm_hinglish",  DESC_M, "Theek hai, kuch nahi likha."),
    ("02_udhaar_hinglish",   DESC_M, "Haan ji, Sharma ji ke khaate mein do sau rupaye ka udhaar likh diya."),
    ("03_lowstock_hinglish", DESC_M, "Parle-G ka stock kam hai, sirf das packet bache hain."),
    ("04_sales_hinglish",    DESC_M, "Aaj ki bikri chhe hazaar rupaye rahi, sab milaakar."),
    ("05_expiry_hinglish",   DESC_M, "Amul doodh teen din mein expire ho raha hai, pehle bech dijiye."),
    ("06_festival_hinglish", DESC_M, "Diwali aa rahi hai, mithai aur diye ka stock badha lijiye."),
    ("07_devanagari",        DESC_M, "नमस्ते! आपका आज का हिसाब तैयार है। दो सौ रुपये का उधार लिख दिया।"),
    ("08_english",           DESC_M, "Your total sales today are six thousand rupees."),
    ("09_udhaar_female",     DESC_F, "Haan ji, Sharma ji ke khaate mein do sau rupaye ka udhaar likh diya."),
]


def main() -> None:
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"[setup] device={device} gpu={gpu} torch={torch.__version__} "
          f"arch_list={torch.cuda.get_arch_list() if torch.cuda.is_available() else 'n/a'}", flush=True)

    t0 = time.time()
    model = ParlerTTSForConditionalGeneration.from_pretrained(REPO).to(device)
    model.eval()
    tok = AutoTokenizer.from_pretrained(REPO)
    desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
    sr = int(model.config.sampling_rate)
    print(f"[setup] model loaded in {time.time()-t0:.1f}s | sampling_rate={sr}", flush=True)

    rows = []
    for slug, desc, text in SAMPLES:
        torch.manual_seed(0)  # reproducible-ish output per sample
        try:
            t1 = time.time()
            d = desc_tok(desc, return_tensors="pt").to(device)
            p = tok(text, return_tensors="pt").to(device)
            with torch.inference_mode():
                gen = model.generate(
                    input_ids=d.input_ids, attention_mask=d.attention_mask,
                    prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask,
                )
            audio = gen.cpu().numpy().squeeze()
            out = OUT / f"parler_{slug}.wav"
            sf.write(out, audio, sr)
            dur = len(audio) / sr
            dt = time.time() - t1
            rows.append((slug, dur, dt, out.name, text))
            print(f"[ok] {slug:22} {dur:5.2f}s audio in {dt:5.1f}s -> {out.name}", flush=True)
        except Exception as e:  # noqa: BLE001 — keep going, report at end
            rows.append((slug, -1, -1, f"FAILED: {e}", text))
            print(f"[FAIL] {slug}: {e}", flush=True)

    print("\n================ SUMMARY ================")
    for slug, dur, dt, name, text in rows:
        tag = f"{dur:.2f}s" if dur >= 0 else "FAILED"
        print(f"{slug:22} | {tag:>7} | {name}")
        print(f"{'':22} | text: {text}")
    print(f"\nWAVs written to: {OUT}")


if __name__ == "__main__":
    main()
