"""Text-to-speech for Dukaan Saathi — closes the voice loop.

Single engine: **Veena** (``maya-research/veena-tts``, GATED) — a Llama-style LM
that emits SNAC audio codes (decoded by ``hubertsiuzdak/snac_24khz``). It speaks
Hindi, English AND Hinglish / code-mixed text. Needs HF access + a token and the
``snac`` package. Returns ``(sampling_rate, np.ndarray float32)`` ready to hand
straight to ``gr.Audio(type="numpy")``.

Nothing heavy is imported or loaded at import time — the model loads lazily on
first synthesis and is cached in a module global. Any synthesis failure (or empty
input) degrades to a short silence, so the Gradio UI never crashes on the voice
path.
"""

from __future__ import annotations

import logging
import re

import numpy as np
import torch

from dukaan import config, numwords

log = logging.getLogger("dukaan.tts")

# Module-level model cache, populated lazily by the loader below.
_VEENA: tuple | None = None      # (model, tokenizer, snac_model)

# Returned on any failure / empty input so callers always get valid audio.
_SILENCE: tuple[int, np.ndarray] = (16000, np.zeros(1, dtype=np.float32))

# --- Veena control tokens (fixed by the model) + decoder constants ---------
_VN_SOS = 128257          # start of speech
_VN_EOS = 128258          # end of speech
_VN_SOH = 128259          # start of human
_VN_EOH = 128260          # end of human
_VN_SOA = 128261          # start of AI
_VN_EOA = 128262          # end of AI
_VN_AUDIO_BASE = 128266   # first audio-code token id (7 codebooks × 4096)
_VN_SR = 24000            # snac_24khz output sampling rate
_VN_SPEAKERS = ("kavya", "agastya", "maitri", "vinaya")


def _device() -> str:
    """Resolve the configured TTS device, falling back to CPU if CUDA is absent."""
    dev = config.TTS_DEVICE
    if dev.startswith("cuda") and not torch.cuda.is_available():
        log.warning("TTS_DEVICE=%s but CUDA unavailable; using cpu.", dev)
        return "cpu"
    return dev


# ----------------------------------------------------------------- load + decode
def _load_veena() -> tuple:
    """Load + cache Veena (the LM) and the SNAC decoder.

    bf16 by default (~6 GB, fine on the shared GPU); set ``DUKAAN_VEENA_4BIT=1``
    for a ~2-3 GB 4-bit load (needs ``bitsandbytes`` + ``accelerate``). Falls back
    to bf16 if 4-bit deps are missing. Raises on failure so :func:`synthesize`
    returns a short silence instead — we don't swallow the error here.
    """
    global _VEENA
    if _VEENA is not None:
        return _VEENA

    from transformers import AutoModelForCausalLM, AutoTokenizer
    from snac import SNAC

    dev = _device()
    quant = None
    if config.VEENA_4BIT:
        try:
            import bitsandbytes  # noqa: F401 — presence check
            from transformers import BitsAndBytesConfig

            quant = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Veena 4-bit requested but unavailable (%s); using bf16.", exc)

    if quant is not None:
        model = AutoModelForCausalLM.from_pretrained(
            config.VEENA_MODEL, quantization_config=quant,
            device_map="auto", trust_remote_code=True)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            config.VEENA_MODEL, dtype=torch.bfloat16, trust_remote_code=True).to(dev)
    model.eval()
    tok = AutoTokenizer.from_pretrained(config.VEENA_MODEL, trust_remote_code=True)
    snac_model = SNAC.from_pretrained(config.VEENA_SNAC_MODEL).eval().to(dev)
    _VEENA = (model, tok, snac_model)
    return _VEENA


def _decode_snac(audio_tokens: list[int], snac_model) -> "np.ndarray | None":
    """De-interleave Veena's 7-token frames into SNAC's 3 levels and decode → wav.

    Tolerant by design: a trailing partial frame is dropped and out-of-range codes
    are clamped (a single glitchy frame shouldn't lose the whole utterance).
    """
    audio_tokens = audio_tokens[: len(audio_tokens) - (len(audio_tokens) % 7)]
    if not audio_tokens:
        return None
    dev = next(snac_model.parameters()).device
    off = [_VN_AUDIO_BASE + i * 4096 for i in range(7)]
    lvl: list[list[int]] = [[], [], []]
    for i in range(0, len(audio_tokens), 7):
        lvl[0].append(audio_tokens[i] - off[0])
        lvl[1] += [audio_tokens[i + 1] - off[1], audio_tokens[i + 4] - off[4]]
        lvl[2] += [audio_tokens[i + 2] - off[2], audio_tokens[i + 3] - off[3],
                   audio_tokens[i + 5] - off[5], audio_tokens[i + 6] - off[6]]
    codes = [torch.tensor(c, dtype=torch.int32, device=dev).unsqueeze(0).clamp(0, 4095)
             for c in lvl]
    with torch.no_grad():
        audio = snac_model.decode(codes)
    return audio.squeeze().clamp(-1, 1).float().cpu().numpy().astype(np.float32, copy=False)


def _veena_chunks(text: str, max_chars: int = 160) -> list[str]:
    """Split text into <=max_chars chunks at sentence boundaries (। . ! ? newline).

    Veena generates audio autoregressively under a token budget, so a single long
    utterance hits the cap and cuts off after a sentence or two. Synthesising
    sentence-sized chunks (then concatenating) keeps each generation short and
    complete, so the whole reply is spoken.
    """
    parts = re.split(r"(?<=[।.!?\n])\s+", (text or "").strip())
    chunks: list[str] = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not cur:
            cur = p
        elif len(cur) + 1 + len(p) <= max_chars:
            cur = f"{cur} {p}"
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks or [(text or "").strip()]


def _veena_gen_one(text: str, spk: str, model, tok, snac_model):
    """Generate one (sentence-sized) chunk → waveform ndarray, or None."""
    prompt_ids = tok.encode(f"<spk_{spk}> {text}", add_special_tokens=False)
    seq = [_VN_SOH, *prompt_ids, _VN_EOH, _VN_SOA, _VN_SOS]
    input_ids = torch.tensor([seq], device=model.device)
    # ~7 audio tokens/char; ceiling sized to the bounded chunk (not a flat 700,
    # which truncated longer replies) so a full sentence is never cut short.
    max_new = min(int(len(text) * 1.3) * 7 + 64, 1500)
    pad_id = tok.pad_token_id
    if pad_id is None:
        pad_id = tok.eos_token_id if tok.eos_token_id is not None else _VN_EOA
    with torch.no_grad():
        out = model.generate(
            input_ids, max_new_tokens=max_new, do_sample=True,
            temperature=0.4, top_p=0.9, repetition_penalty=1.05,
            pad_token_id=pad_id, eos_token_id=[_VN_EOS, _VN_EOA],
        )
    gen = out[0][len(seq):].tolist()
    audio_tokens = [t for t in gen if _VN_AUDIO_BASE <= t < _VN_AUDIO_BASE + 7 * 4096]
    if not audio_tokens:
        return None
    return _decode_snac(audio_tokens, snac_model)


def _synth_veena(text: str, speaker: str | None = None) -> tuple[int, np.ndarray]:
    """Speak ``text`` with Veena → ``(24000, waveform float32)``.

    Long text is chunked by sentence and concatenated (with a short pause) so the
    full reply is spoken — a single generation hits the token cap and stops after a
    sentence or two. Returns silence (never raises here) on failure.
    """
    model, tok, snac_model = _load_veena()
    spk = (speaker or config.VEENA_SPEAKER or "agastya").strip().lower()
    if spk not in _VN_SPEAKERS:
        spk = "agastya"

    parts: list[np.ndarray] = []
    for chunk in _veena_chunks(text):
        try:
            a = _veena_gen_one(chunk, spk, model, tok, snac_model)
        except Exception as exc:  # noqa: BLE001 — one bad chunk shouldn't kill the rest
            log.warning("Veena chunk failed (%s); skipping.", exc)
            a = None
        if a is not None and a.size > 1:
            parts.append(a)

    if not parts:
        log.debug("Veena: no audio for %r — returning silence.", (text or "")[:60])
        return _SILENCE
    if len(parts) == 1:
        return _VN_SR, np.atleast_1d(parts[0])
    gap = np.zeros(int(_VN_SR * 0.14), dtype=np.float32)  # ~0.14s pause between chunks
    joined = parts[0]
    for a in parts[1:]:
        joined = np.concatenate([joined, gap, a])
    return _VN_SR, np.atleast_1d(joined)


def _clean_for_tts(text: str) -> str:
    """Strip markdown and voice numbers / ``₹`` as Hindi words (clearer in speech)."""
    t = re.sub(r"[*_`#>]+", " ", text)
    t = re.sub(r"(?m)^\s*[-•]\s*", "", t)
    t = t.replace("|", " ").replace("—", " ")
    t = numwords.digits_to_words(t)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------- public API
def synthesize(text: str) -> tuple[int, np.ndarray]:
    """Speak ``text`` with Veena → ``(sampling_rate, waveform float32)``.

    Veena speaks Hindi / English / Hinglish. Empty text yields a short silence, and
    *any* failure yields ``(16000, zeros)`` so the UI never crashes on playback.
    """
    if text is None or not text.strip():
        return _SILENCE
    text = _clean_for_tts(text.strip())
    if not text:
        return _SILENCE
    try:
        return _synth_veena(text)
    except Exception as exc:  # noqa: BLE001 — never break the UI on the voice path
        log.warning("Veena TTS failed (%s); returning silence.", exc)
        return _SILENCE


def warmup() -> None:
    """Pre-load Veena so the first real call isn't slow. Never raises."""
    try:
        _load_veena()
        log.info("TTS warmup complete (veena).")
    except Exception as exc:  # noqa: BLE001
        log.warning("Veena TTS warmup failed (%s).", exc)
