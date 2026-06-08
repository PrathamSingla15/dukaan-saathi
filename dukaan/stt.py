"""Multilingual auto-detect STT for Dukaan Saathi via faster-whisper (CTranslate2).

ffmpeg-free by design: `faster_whisper`'s `WhisperModel.transcribe` accepts a raw
**float32, 16 kHz, mono** numpy array directly, so we resample with
`scipy.signal.resample_poly` instead of shelling out to ffmpeg (no system ffmpeg
on the cluster). Models are loaded lazily and cached on first use — never at
import time, and never on the GPU until something actually transcribes.

`transcribe()` accepts whatever `gr.Audio(type="numpy")` hands back —
``(sr, np.ndarray)`` — as well as a file path or a bare 16 kHz array.
Returns a :class:`TranscribeResult` dataclass with structured quality signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

import numpy as np
from scipy.signal import resample_poly

from dukaan import config

if TYPE_CHECKING:
    from faster_whisper import WhisperModel as _WhisperModel

# Whisper's native sample rate.
_TARGET_SR = 16000

# Module-level model caches (populated lazily).
_MODEL = None
_HINDI_MODEL: "_WhisperModel | None | bool" = False  # False = not yet attempted


@dataclass(frozen=True)
class TranscribeResult:
    """Structured result returned by :func:`transcribe`.

    Attributes:
        text: Transcribed text (empty string on failure).
        language: BCP-47 language code detected by the model (e.g. "hi", "en").
        confidence: Language detection probability in [0, 1].
        no_speech: Mean no-speech probability across segments (1.0 = all silence).
        ok: True when text is non-empty AND confidence/no_speech thresholds pass.
        reason: Machine-readable failure tag, empty on success.
    """

    text: str
    language: str
    confidence: float
    no_speech: float
    ok: bool
    reason: str = field(default="")


def get_model():
    """Return the cached `WhisperModel`, loading it on first call.

    Tries the configured device/compute first (e.g. CUDA + float16); on any
    failure (no GPU, cuDNN/CTranslate2 issues, OOM) it falls back to a CPU int8
    model and caches that instead.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from faster_whisper import WhisperModel

    try:
        _MODEL = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
    except Exception:
        # CUDA / compute_type unavailable -> safe CPU fallback.
        _MODEL = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _MODEL


def get_hindi_model() -> "_WhisperModel | None":
    """Return the cached Hindi-optimised `WhisperModel`, or None if unavailable.

    Uses :data:`config.STT_HINDI_MODEL` as the model identifier.  Returns
    ``None`` (and caches that) when:

    * ``config.STT_HINDI_MODEL`` is empty / falsy, or
    * the model is not yet downloaded (raises on load), or
    * any other exception occurs.

    Never raises.
    """
    global _HINDI_MODEL
    # False means we haven't tried yet; None means we tried and failed.
    if _HINDI_MODEL is not False:
        return _HINDI_MODEL  # type: ignore[return-value]
    if not config.STT_HINDI_MODEL:
        _HINDI_MODEL = None
        return None
    try:
        from faster_whisper import WhisperModel

        _HINDI_MODEL = WhisperModel(
            config.STT_HINDI_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
    except Exception:
        try:
            from faster_whisper import WhisperModel as _WM  # noqa: F401

            _HINDI_MODEL = _WM(config.STT_HINDI_MODEL, device="cpu", compute_type="int8")
        except Exception:
            _HINDI_MODEL = None
    return _HINDI_MODEL  # type: ignore[return-value]


def to_whisper_input(sr: int, y: np.ndarray) -> np.ndarray:
    """Normalise raw audio to a contiguous float32 mono 16 kHz array.

    Handles int16/int32/float input (ints are scaled to [-1, 1]), collapses
    stereo to mono, and resamples to 16 kHz when needed.
    """
    y = np.asarray(y)

    # Scale integer PCM to [-1, 1]; cast any float dtype down to float32.
    if np.issubdtype(y.dtype, np.integer):
        max_val = float(np.iinfo(y.dtype).max)
        y = y.astype(np.float32) / max_val
    else:
        y = y.astype(np.float32)

    # Stereo (or multi-channel) -> mono.
    if y.ndim > 1:
        y = y.mean(axis=1)

    # Resample to 16 kHz only if necessary.
    if sr and sr != _TARGET_SR:
        y = resample_poly(y, _TARGET_SR, int(sr)).astype(np.float32)

    return np.ascontiguousarray(y, dtype=np.float32)


def transcribe(audio, language: str | None = None) -> TranscribeResult:
    """Transcribe speech to text, returning a structured :class:`TranscribeResult`.

    `audio` may be a ``(sr, np.ndarray)`` tuple (as from `gr.Audio`), a file
    path string, or a bare numpy array already at 16 kHz.

    Language resolution order:
      1. explicit ``language`` argument
      2. :data:`config.STT_LANGUAGE` (env override)
      3. ``None`` → faster-whisper auto-detects

    When auto-detect yields Hindi with probability >= :data:`config.STT_HINDI_THRESHOLD`
    and a Hindi model is available, the audio is re-transcribed with that model.
    """
    # --- empty / None guard ---------------------------------------------------
    if audio is None:
        return TranscribeResult("", "", 0.0, 1.0, ok=False, reason="empty_audio")

    # --- resolve input shape -> arr ready for the model ----------------------
    if isinstance(audio, tuple):
        sr, y = audio
        if y is None or (hasattr(y, "__len__") and len(y) == 0):
            return TranscribeResult("", "", 0.0, 1.0, ok=False, reason="empty_audio")
        arr = to_whisper_input(int(sr), y)
    elif isinstance(audio, str):
        if not audio:
            return TranscribeResult("", "", 0.0, 1.0, ok=False, reason="empty_audio")
        arr = audio  # let faster-whisper read the file (uses PyAV, not ffmpeg)
    else:
        y = np.asarray(audio)
        if y.size == 0:
            return TranscribeResult("", "", 0.0, 1.0, ok=False, reason="empty_audio")
        arr = to_whisper_input(_TARGET_SR, y)

    # --- language: "" -> None so faster-whisper auto-detects -----------------
    lang: str | None = language or (config.STT_LANGUAGE or None)

    # --- primary transcription -----------------------------------------------
    try:
        segs, info = get_model().transcribe(arr, language=lang, beam_size=5, vad_filter=True)
        segs = list(segs)
    except Exception:
        return TranscribeResult("", "", 0.0, 1.0, ok=False, reason="stt_error")

    # --- optional Hindi 2nd pass ---------------------------------------------
    detected_lang: str = getattr(info, "language", "") or ""
    lang_prob: float = float(getattr(info, "language_probability", 0.0))

    if (
        lang is None
        and detected_lang == "hi"
        and lang_prob >= config.STT_HINDI_THRESHOLD
        and get_hindi_model() is not None
    ):
        try:
            hi_segs, hi_info = get_hindi_model().transcribe(  # type: ignore[union-attr]
                arr, language="hi", beam_size=5, vad_filter=True
            )
            segs = list(hi_segs)
            info = hi_info
            detected_lang = getattr(hi_info, "language", "hi") or "hi"
            lang_prob = float(getattr(hi_info, "language_probability", lang_prob))
        except Exception:
            pass  # fall through with primary-pass results

    # --- aggregate results ---------------------------------------------------
    text = "".join(s.text for s in segs).strip()
    conf = lang_prob
    no_speech = (
        mean(getattr(s, "no_speech_prob", 0.0) for s in segs) if segs else 1.0
    )

    # --- quality gates -------------------------------------------------------
    if not text:
        return TranscribeResult(text, detected_lang, conf, no_speech, ok=False, reason="empty_text")
    if conf < config.STT_MIN_CONFIDENCE:
        return TranscribeResult(text, detected_lang, conf, no_speech, ok=False, reason="low_confidence")
    if no_speech > config.STT_MAX_NOSPEECH:
        return TranscribeResult(text, detected_lang, conf, no_speech, ok=False, reason="no_speech")

    return TranscribeResult(text, detected_lang, conf, no_speech, ok=True, reason="")


def transcribe_text(audio, language: str | None = None) -> str:
    """Back-compat shim: transcribe audio and return just the text string."""
    return transcribe(audio, language).text


def warmup() -> None:
    """Eagerly load the model so the first real transcription isn't slow."""
    get_model()
