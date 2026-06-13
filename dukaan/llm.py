"""Client boundary to the local Gemma-4-12B served by llama.cpp (`llama-server`).

Two access paths, both pointed at the same OpenAI-compatible endpoint:

* `make_chat_model()` — a LangChain `ChatOpenAI` **instance** for deepagents.
  We pass an instance (not an ``"openai:..."`` string) on purpose: that keeps
  deepagents on the Chat-Completions API instead of the OpenAI Responses API,
  which llama.cpp does not implement.
* `raw_client()` / `chat()` / `vision_extract()` — the `openai` SDK for direct,
  one-shot calls (normalisation, OCR, summarising, drafting) that don't need the
  full agent loop.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Any

import httpx

from dukaan import config


def _extra_body() -> dict:
    """Per-request body sent to llama-server. Toggles Gemma-4's thinking mode
    (off by default → fast, direct answers and dependable tool calls)."""
    return {"chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING}}


# --------------------------------------------------------------- deepagents model
def make_chat_model(temperature: float | None = None, **overrides):
    """Return a `ChatOpenAI` bound to the local llama-server (for `create_deep_agent`)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE if temperature is None else temperature,
        timeout=config.LLM_REQUEST_TIMEOUT,
        max_retries=2,
        extra_body=_extra_body(),
        **overrides,
    )


# ------------------------------------------------------------------- raw SDK calls
def raw_client():
    from openai import OpenAI

    return OpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        timeout=config.LLM_REQUEST_TIMEOUT,
        max_retries=2,
    )


def chat(messages: list[dict], temperature: float = 0.0,
         max_tokens: int | None = 1024, **kw: Any) -> str:
    """One-shot chat completion → assistant text."""
    kw.setdefault("extra_body", _extra_body())
    resp = raw_client().chat.completions.create(
        model=config.LLM_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens, **kw,
    )
    return (resp.choices[0].message.content or "").strip()


def complete(prompt: str, system: str | None = None, **kw: Any) -> str:
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return chat(msgs, **kw)


# ------------------------------------------------------------------------- vision
# Cap the longest edge sent to the vision model. Phone photos are often 3000-4000px;
# a small (≤12B) vision model reads a right-sized image MORE accurately than a giant
# one, and the base64 payload shrinks a lot. Never upscales (PIL.thumbnail downscales).
_VISION_MAX_EDGE = 1600


def _preprocess_for_vision(img):
    """Auto-orient, right-size and gently sharpen a PIL image for OCR.

    EXIF auto-rotate (phone bills are frequently sideways), downscale to
    ``_VISION_MAX_EDGE``, and a mild contrast/sharpness bump so faint ink / thermal
    print reads better. Conservative on purpose — a clean image stays clean.
    """
    from PIL import ImageEnhance, ImageOps

    img = ImageOps.exif_transpose(img)                    # honour camera rotation
    img = img.convert("RGB")
    img.thumbnail((_VISION_MAX_EDGE, _VISION_MAX_EDGE))   # in-place, downscale only
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Sharpness(img).enhance(1.10)
    return img


def _image_to_data_uri(image: "Any") -> str:
    """Accept a PIL.Image, raw bytes, or a path; auto-orient + right-size; return a
    base64 JPEG data URI. Falls back to a raw passthrough if PIL can't open it."""
    from PIL import Image

    try:
        if isinstance(image, (str, Path)):
            img = Image.open(image)
        elif isinstance(image, (bytes, bytearray)):
            img = Image.open(io.BytesIO(bytes(image)))
        else:                       # assume PIL.Image.Image
            img = image
        img = _preprocess_for_vision(img)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except Exception:  # noqa: BLE001 — last resort: send the bytes we were given
        if isinstance(image, (bytes, bytearray)):
            raw = bytes(image)
        elif isinstance(image, (str, Path)):
            raw = Path(image).read_bytes()
        else:
            raise
        return f"data:image/png;base64,{base64.b64encode(raw).decode()}"


def vision_extract(image: "Any", prompt: str, temperature: float = 0.0,
                   max_tokens: int = 1024, response_format: dict | None = None) -> str:
    """Send an image + instruction to Gemma-4's vision path; return the text reply.

    `image` may be a PIL image, bytes, or a file path. Pass
    ``response_format={"type": "json_object"}`` to force well-formed JSON (used by
    the challan / khata OCR); if the endpoint doesn't support it the call is retried
    once without it, so OCR still works (free-text + salvage parse).
    """
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_to_data_uri(image)}},
    ]

    def _call(rf: dict | None) -> str:
        kw: dict = {"extra_body": _extra_body()}
        if rf is not None:
            kw["response_format"] = rf
        resp = raw_client().chat.completions.create(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=temperature, max_tokens=max_tokens, **kw,
        )
        return (resp.choices[0].message.content or "").strip()

    try:
        return _call(response_format)
    except Exception:
        if response_format is not None:
            return _call(None)      # endpoint lacks response_format -> degrade gracefully
        raise


# --------------------------------------------------------------------- health/wait
def _roots(base_url: str | None = None) -> tuple[str, str]:
    url = (base_url or config.LLM_BASE_URL).rstrip("/")
    root = url[:-3].rstrip("/") if url.endswith("/v1") else url
    return root, url


def health(base_url: str | None = None) -> bool:
    """True if llama-server is up (probes /health then /v1/models)."""
    root, v1 = _roots(base_url)
    for probe in (f"{root}/health", f"{v1}/models"):
        try:
            if httpx.get(probe, timeout=3).status_code == 200:
                return True
        except Exception:
            continue
    return False


def wait_until_ready(timeout: float = 600, interval: float = 3) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if health():
            return True
        time.sleep(interval)
    return False
