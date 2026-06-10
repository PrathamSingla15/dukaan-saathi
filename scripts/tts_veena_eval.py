"""Evaluate maya-research/veena-tts on Hinglish / Devanagari / English / code-mixed.

Mirrors the Veena reference snippet (Llama-style LM that emits SNAC audio codes,
decoded by hubertsiuzdak/snac_24khz @ 24 kHz). Writes one WAV per sample into
``logs/`` for side-by-side judging against the indic-parler-tts outputs.

Loads in 4-bit (as the model card suggests) and FALLS BACK to bf16 if bitsandbytes
is unavailable on this CUDA build — a 48 GB GPU has ample room for bf16, which is
also the model's best-quality path. Run in the isolated ``parler-eval`` venv on a GPU.
"""

from __future__ import annotations

import time
from pathlib import Path

import soundfile as sf
import torch
from snac import SNAC
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO = "maya-research/veena-tts"
SNAC_REPO = "hubertsiuzdak/snac_24khz"
OUT = Path("/home/shivank_g/projects/small_build_hackathon/dukaan-saathi/logs")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000

# Control token IDs (fixed for Veena)
START_OF_SPEECH_TOKEN = 128257
END_OF_SPEECH_TOKEN = 128258
START_OF_HUMAN_TOKEN = 128259
END_OF_HUMAN_TOKEN = 128260
START_OF_AI_TOKEN = 128261
END_OF_AI_TOKEN = 128262
AUDIO_CODE_BASE_OFFSET = 128266

# (slug, speaker, text) — same lines as the parler eval so they're comparable,
# plus a Devanagari+Latin code-mixed line (Veena's headline strength).
SAMPLES = [
    ("01_confirm_hinglish",  "agastya", "Theek hai, kuch nahi likha."),
    ("02_udhaar_hinglish",   "agastya", "Haan ji, Sharma ji ke khaate mein do sau rupaye ka udhaar likh diya."),
    ("03_lowstock_hinglish", "agastya", "Parle-G ka stock kam hai, sirf das packet bache hain."),
    ("04_sales_hinglish",    "agastya", "Aaj ki bikri chhe hazaar rupaye rahi, sab milaakar."),
    ("05_expiry_hinglish",   "agastya", "Amul doodh teen din mein expire ho raha hai, pehle bech dijiye."),
    ("06_festival_hinglish", "agastya", "Diwali aa rahi hai, mithai aur diye ka stock badha lijiye."),
    ("07_devanagari",        "agastya", "नमस्ते! आपका आज का हिसाब तैयार है। दो सौ रुपये का उधार लिख दिया।"),
    ("08_english",           "agastya", "Your total sales today are six thousand rupees."),
    ("09_udhaar_female",     "kavya",   "Haan ji, Sharma ji ke khaate mein do sau rupaye ka udhaar likh diya."),
    ("10_codemix",           "maitri",  "Sharma ji ka pura hisaab ready hai — कल रात को ही मैंने पूरा खाता check kar liya."),
]


def load_model():
    """4-bit if bitsandbytes works on this CUDA build, else bf16 (best quality)."""
    try:
        from transformers import BitsAndBytesConfig
        qcfg = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
        )
        m = AutoModelForCausalLM.from_pretrained(
            REPO, quantization_config=qcfg, device_map="auto", trust_remote_code=True)
        return m, "4-bit-nf4"
    except Exception as e:  # noqa: BLE001
        print(f"[load] 4-bit failed ({type(e).__name__}: {str(e)[:80]}); using bf16.", flush=True)
        m = AutoModelForCausalLM.from_pretrained(
            REPO, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True)
        return m, "bf16"


def decode_snac_tokens(snac_tokens, snac_model):
    """De-interleave the 7-token frames into SNAC's 3 hierarchical levels + decode."""
    if not snac_tokens or len(snac_tokens) % 7 != 0:
        return None
    snac_device = next(snac_model.parameters()).device
    codes_lvl = [[] for _ in range(3)]
    off = [AUDIO_CODE_BASE_OFFSET + i * 4096 for i in range(7)]
    for i in range(0, len(snac_tokens), 7):
        codes_lvl[0].append(snac_tokens[i] - off[0])
        codes_lvl[1].append(snac_tokens[i + 1] - off[1])
        codes_lvl[1].append(snac_tokens[i + 4] - off[4])
        codes_lvl[2].append(snac_tokens[i + 2] - off[2])
        codes_lvl[2].append(snac_tokens[i + 3] - off[3])
        codes_lvl[2].append(snac_tokens[i + 5] - off[5])
        codes_lvl[2].append(snac_tokens[i + 6] - off[6])
    hier = []
    for lvl in codes_lvl:
        t = torch.tensor(lvl, dtype=torch.int32, device=snac_device).unsqueeze(0)
        if torch.any((t < 0) | (t > 4095)):
            raise ValueError("Invalid SNAC token values")
        hier.append(t)
    with torch.no_grad():
        audio_hat = snac_model.decode(hier)
    return audio_hat.squeeze().clamp(-1, 1).cpu().numpy()


def generate_speech(text, model, tokenizer, snac_model, speaker="kavya",
                    temperature=0.4, top_p=0.9):
    prompt_tokens = tokenizer.encode(f"<spk_{speaker}> {text}", add_special_tokens=False)
    input_tokens = [START_OF_HUMAN_TOKEN, *prompt_tokens, END_OF_HUMAN_TOKEN,
                    START_OF_AI_TOKEN, START_OF_SPEECH_TOKEN]
    input_ids = torch.tensor([input_tokens], device=model.device)
    max_tokens = min(int(len(text) * 1.3) * 7 + 21, 700)
    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id or END_OF_AI_TOKEN
    with torch.no_grad():
        output = model.generate(
            input_ids, max_new_tokens=max_tokens, do_sample=True,
            temperature=temperature, top_p=top_p, repetition_penalty=1.05,
            pad_token_id=pad_id, eos_token_id=[END_OF_SPEECH_TOKEN, END_OF_AI_TOKEN])
    gen = output[0][len(input_tokens):].tolist()
    snac_tokens = [t for t in gen if AUDIO_CODE_BASE_OFFSET <= t < (AUDIO_CODE_BASE_OFFSET + 7 * 4096)]
    if not snac_tokens:
        raise ValueError("no audio tokens generated")
    return decode_snac_tokens(snac_tokens, snac_model)


def main() -> None:
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"[setup] gpu={gpu} torch={torch.__version__}", flush=True)
    t0 = time.time()
    model, mode = load_model()
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(REPO, trust_remote_code=True)
    snac_model = SNAC.from_pretrained(SNAC_REPO).eval().cuda()
    print(f"[setup] veena loaded ({mode}) + SNAC in {time.time()-t0:.1f}s", flush=True)

    rows = []
    for slug, speaker, text in SAMPLES:
        torch.manual_seed(0)
        try:
            t1 = time.time()
            audio = generate_speech(text, model, tokenizer, snac_model, speaker=speaker)
            out = OUT / f"veena_{slug}_{speaker}.wav"
            sf.write(out, audio, SR)
            dur, dt = len(audio) / SR, time.time() - t1
            rows.append((slug, speaker, dur, dt, out.name, text))
            print(f"[ok] {slug:22} {speaker:8} {dur:5.2f}s in {dt:5.1f}s -> {out.name}", flush=True)
        except Exception as e:  # noqa: BLE001
            rows.append((slug, speaker, -1, -1, f"FAILED: {e}", text))
            print(f"[FAIL] {slug} ({speaker}): {e}", flush=True)

    print("\n================ VEENA SUMMARY ================")
    print(f"load mode: {mode}")
    for slug, speaker, dur, dt, name, text in rows:
        tag = f"{dur:.2f}s" if dur >= 0 else "FAILED"
        print(f"{slug:22} {speaker:8} | {tag:>7} | {name}")
        print(f"{'':32}| text: {text}")
    print(f"\nWAVs written to: {OUT}")


if __name__ == "__main__":
    main()
