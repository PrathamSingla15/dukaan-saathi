"""Hindi text-to-speech for Dukaan Saathi — closes the voice loop.

Two engines, both ffmpeg-free, returning ``(sampling_rate, np.ndarray float32)``
ready to hand straight to ``gr.Audio(type="numpy")``:

* **mms** (default, open) — ``facebook/mms-tts-hin`` via transformers' VITS.
  Tiny, fast, works out of the box; transformers auto-romanises Devanagari.
* **parler** (optional, GATED) — ``ai4bharat/indic-parler-tts``. Most natural
  Hindi but needs HF access approval + the ``parler-tts`` package. If the import
  or model load fails we log a warning and fall back to **mms**.

Nothing heavy is imported or loaded at import time — models load lazily on first
synthesis and are cached in module globals. Any failure degrades to a short
silence so the Gradio UI never crashes.
"""

from __future__ import annotations

import logging
import re

import numpy as np
import torch

from dukaan import config, numwords

log = logging.getLogger("dukaan.tts")

# Module-level model caches, populated lazily by the loaders below.
_MMS: tuple | None = None        # (model, tokenizer, sampling_rate)
_PARLER: tuple | None = None     # (model, tokenizer, description_tokenizer, sampling_rate)

# Returned on any failure / empty input so callers always get valid audio.
_SILENCE: tuple[int, np.ndarray] = (16000, np.zeros(1, dtype=np.float32))


def _device() -> str:
    """Resolve the configured TTS device, falling back to CPU if CUDA is absent."""
    dev = config.TTS_DEVICE
    if dev.startswith("cuda") and not torch.cuda.is_available():
        log.warning("TTS_DEVICE=%s but CUDA unavailable; using cpu.", dev)
        return "cpu"
    return dev


# ----------------------------------------------------------------------- loaders
def _load_mms() -> tuple:
    """Load + cache the MMS-TTS VITS model and tokenizer. Falls back to CPU."""
    global _MMS
    if _MMS is not None:
        return _MMS

    from transformers import AutoTokenizer, VitsModel

    tok = AutoTokenizer.from_pretrained(config.MMS_MODEL)
    model = VitsModel.from_pretrained(config.MMS_MODEL)
    try:
        model = model.to(_device())
    except Exception as exc:  # e.g. CUDA OOM / driver mismatch
        log.warning("MMS to(%s) failed (%s); using cpu.", _device(), exc)
        model = model.to("cpu")
    model.eval()
    _MMS = (model, tok, int(model.config.sampling_rate))
    return _MMS


def _load_parler() -> tuple:
    """Load + cache Parler-TTS (lazy, gated).

    Raises on missing package or load failure so :func:`synthesize` can fall
    back to MMS — we deliberately do not swallow the error here.
    """
    global _PARLER
    if _PARLER is not None:
        return _PARLER

    from parler_tts import ParlerTTSForConditionalGeneration  # may raise ImportError
    from transformers import AutoTokenizer

    dev = _device()
    model = ParlerTTSForConditionalGeneration.from_pretrained(config.PARLER_MODEL).to(dev)
    model.eval()
    # Parler uses two tokenizers: a text one for the prompt and (sometimes a
    # separate) one for the speaker description; the same repo serves both.
    tok = AutoTokenizer.from_pretrained(config.PARLER_MODEL)
    desc_name = getattr(model.config, "text_encoder", None)
    desc_name = getattr(desc_name, "_name_or_path", None) or config.PARLER_MODEL
    try:
        desc_tok = AutoTokenizer.from_pretrained(desc_name)
    except Exception:
        desc_tok = tok
    sr = int(model.config.sampling_rate)
    _PARLER = (model, tok, desc_tok, sr)
    return _PARLER


# -------------------------------------------------------------------- synthesis
def _synth_mms(text: str) -> tuple[int, np.ndarray]:
    model, tok, sr = _load_mms()
    dev = next(model.parameters()).device
    inputs = tok(text, return_tensors="pt").to(dev)
    with torch.no_grad():
        wav = model(**inputs).waveform
    wav = wav.squeeze().float().cpu().numpy().astype(np.float32, copy=False)
    return sr, np.atleast_1d(wav)


def _synth_parler(text: str) -> tuple[int, np.ndarray]:
    model, tok, desc_tok, sr = _load_parler()
    dev = next(model.parameters()).device
    desc_ids = desc_tok(config.PARLER_DESCRIPTION, return_tensors="pt").to(dev)
    prompt_ids = tok(text, return_tensors="pt").to(dev)
    with torch.no_grad():
        wav = model.generate(
            input_ids=desc_ids.input_ids,
            attention_mask=getattr(desc_ids, "attention_mask", None),
            prompt_input_ids=prompt_ids.input_ids,
            prompt_attention_mask=getattr(prompt_ids, "attention_mask", None),
        )
    wav = wav.squeeze().float().cpu().numpy().astype(np.float32, copy=False)
    return sr, np.atleast_1d(wav)


def _clean_for_tts(text: str) -> str:
    """Strip markdown and voice numbers/``₹`` as Hindi words (MMS can't speak digits)."""
    t = re.sub(r"[*_`#>]+", " ", text)
    t = re.sub(r"(?m)^\s*[-•]\s*", "", t)
    t = t.replace("|", " ").replace("—", " ")
    t = numwords.digits_to_words(t)
    return re.sub(r"\s+", " ", t).strip()


def synthesize(text: str, engine: str | None = None) -> tuple[int, np.ndarray]:
    """Speak ``text`` in Hindi → ``(sampling_rate, waveform float32)``.

    ``engine`` defaults to :data:`config.TTS_ENGINE`. ``"parler"`` is attempted
    first and silently falls back to ``"mms"`` if unavailable. Empty text yields
    a short silence, and *any* failure yields ``(16000, zeros)`` so the UI never
    crashes on audio playback.
    """
    if text is None or not text.strip():
        return _SILENCE

    text = _clean_for_tts(text.strip())
    if not text:
        return _SILENCE
    engine = (engine or config.TTS_ENGINE or "mms").lower()

    if engine == "parler":
        try:
            return _synth_parler(text)
        except Exception as exc:
            log.warning("Parler TTS unavailable (%s); falling back to mms.", exc)
            engine = "mms"

    try:
        return _synth_mms(text)
    except Exception as exc:
        log.exception("MMS TTS failed (%s); returning silence.", exc)
        return _SILENCE


# ----------------------------------------------------------------------- warmup
def warmup(engine: str | None = None) -> None:
    """Pre-load the chosen engine's model so the first real call is fast.

    Never raises: a warm-up failure (e.g. Parler gated) is logged, and for
    Parler we fall back to warming MMS so synthesis stays responsive.
    """
    engine = (engine or config.TTS_ENGINE or "mms").lower()
    try:
        if engine == "parler":
            _load_parler()
        else:
            _load_mms()
        log.info("TTS warmup complete (engine=%s).", engine)
    except Exception as exc:
        log.warning("TTS warmup for engine=%s failed (%s).", engine, exc)
        if engine == "parler":
            try:
                _load_mms()
                log.info("TTS warmup fell back to mms.")
            except Exception as exc2:
                log.warning("TTS mms warmup also failed (%s).", exc2)
