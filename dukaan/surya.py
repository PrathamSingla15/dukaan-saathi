"""Surya OCR 2 pre-pass client.

A thin HTTP seam over a remote Surya OCR service (``datalab-to/surya-ocr-2``,
served on a GPU — see the out-of-repo ``serve_surya_local.py``). Given a bill /
khata image, it returns Surya's recognized plain text, which the caller folds into
the vision prompt as grounding so Gemma reads names / numbers more accurately.

Backend-agnostic: it only needs ``config.SURYA_BASE_URL`` (a Surya ``/ocr``
endpoint — local, the cluster tunnel, or Modal). When that is unset OR the call
fails, :func:`ocr_text` returns ``""`` so the OCR pipeline simply runs Gemma-only.
Never raises. Mirrors the ``stt`` / ``tts`` remote seams.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from pathlib import Path
from typing import Any

from dukaan import config

log = logging.getLogger("dukaan.surya")

# Circuit breaker: surya-ocr-2 on llama.cpp occasionally enters a generation loop
# that wedges the backend. After a couple of consecutive failures we skip Surya for
# a cooldown so the OCR flow never keeps paying the timeout — it self-heals on retry.
_FAILS = 0
_COOLDOWN_UNTIL = 0.0
_MAX_FAILS = 2
_COOLDOWN_S = 120.0

# Cap the image we send (Surya is a doc-OCR VLM; right-sized images read fine and
# the base64 payload over the tunnel stays small).
_MAX_EDGE = 2048
_TIMEOUT = float(getattr(config, "SURYA_REQUEST_TIMEOUT", 120) or 120)


def _to_jpeg_b64(image: Any) -> str | None:
    """PIL image / bytes / path -> base64 JPEG, EXIF-oriented + right-sized. None on failure."""
    try:
        from PIL import Image, ImageOps

        if isinstance(image, (bytes, bytearray)):
            img = Image.open(io.BytesIO(bytes(image)))
        elif isinstance(image, (str, Path)):
            img = Image.open(image)
        else:  # assume PIL.Image
            img = image
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((_MAX_EDGE, _MAX_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:  # noqa: BLE001
        log.warning("Surya: could not encode image (%s).", exc)
        return None


def _looks_degenerate(text: str) -> bool:
    """True if the OCR text looks like a repetition loop (don't feed garbage to the LLM)."""
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) >= 4 and len(set(lines)) * 2 <= len(lines)


def ocr_text(image: Any) -> str:
    """Return Surya's recognized text for ``image``, or ``""`` (no-op) when Surya is
    not configured / unreachable / slow / degenerate. Never raises."""
    global _FAILS, _COOLDOWN_UNTIL
    base = (config.SURYA_BASE_URL or "").strip()
    if not base or image is None:
        return ""
    if time.time() < _COOLDOWN_UNTIL:   # circuit open — skip Surya, go Gemma-only
        return ""
    b64 = _to_jpeg_b64(image)
    if not b64:
        return ""
    try:
        import httpx

        r = httpx.post(base, json={"image_b64": b64}, timeout=_TIMEOUT)
        r.raise_for_status()
        text = (r.json().get("text") or "").strip()
        _FAILS = 0
        if not text or _looks_degenerate(text):
            return ""   # drop empty / looped output rather than mislead the LLM
        return text
    except Exception as exc:  # noqa: BLE001 — OCR grounding is best-effort
        _FAILS += 1
        if _FAILS >= _MAX_FAILS:
            _COOLDOWN_UNTIL = time.time() + _COOLDOWN_S
            _FAILS = 0
            log.warning("Surya OCR unhealthy; skipping it for %ss.", int(_COOLDOWN_S))
        else:
            log.warning("Surya OCR call failed (%s); continuing without it.", exc)
        return ""
