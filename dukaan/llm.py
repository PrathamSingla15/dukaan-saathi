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
def _image_to_data_uri(image: "Any") -> str:
    """Accept a PIL.Image, raw bytes, or a path; return a base64 data URI."""
    if isinstance(image, (str, Path)):
        p = Path(image)
        data = p.read_bytes()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp", "gif": "image/gif"}.get(p.suffix.lower().lstrip("."), "image/png")
    elif isinstance(image, (bytes, bytearray)):
        data, mime = bytes(image), "image/png"
    else:  # assume PIL.Image.Image
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=92)
        data, mime = buf.getvalue(), "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def vision_extract(image: "Any", prompt: str, temperature: float = 0.0,
                   max_tokens: int = 1024) -> str:
    """Send an image + instruction to Gemma-4's vision path; return the text reply.
    `image` may be a PIL image, bytes, or a file path."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_to_data_uri(image)}},
    ]
    resp = raw_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=temperature, max_tokens=max_tokens,
        extra_body=_extra_body(),
    )
    return (resp.choices[0].message.content or "").strip()


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
