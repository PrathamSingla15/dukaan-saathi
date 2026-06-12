#!/usr/bin/env python3
"""Pre-warm the Modal-hosted models before a live demo / judging run.

The flagship Space calls ONE Modal endpoint for everything — the LLM (+vision/OCR),
STT (Whisper) and TTS (Veena). With scale-to-zero the first request after idle pays
a cold start (GGUF + model load) that can take a minute or two. Run this ~2 minutes
before filming so the first real turn the judge sees is instant.

Usage
-----
    uv run python scripts/prewarm.py                          # uses DUKAAN_*_BASE_URL / config
    uv run python scripts/prewarm.py https://<ws>--dukaan-llm-serve.modal.run

It exercises the three inference paths (a 1-token completion, a short STT call, a
short TTS call) through the normal dukaan client code, so it warms whatever the env
points at — Modal in production, or a local llama-server in dev. Read-only: it
records nothing to any database.
"""
from __future__ import annotations

import os
import sys
import time


def _route_to(base: str) -> None:
    """Point LLM/STT/TTS at one Modal base URL (modal_app.py serves all three)."""
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    os.environ["DUKAAN_LLM_BASE_URL"] = base + "/v1"
    os.environ["DUKAAN_STT_BASE_URL"] = base + "/stt"
    os.environ["DUKAAN_TTS_BASE_URL"] = base + "/tts"


def _timed(label: str, fn) -> bool:
    t0 = time.perf_counter()
    try:
        fn()
        print(f"  [ok]   {label:<4s} {time.perf_counter() - t0:6.1f}s")
        return True
    except Exception as e:  # noqa: BLE001 — report, never crash the warmup
        print(f"  [FAIL] {label:<4s} {time.perf_counter() - t0:6.1f}s  {type(e).__name__}: {e}")
        return False


def main() -> int:
    if len(sys.argv) > 1:
        _route_to(sys.argv[1])

    # import AFTER routing so config reads the endpoints we just set
    import numpy as np

    from dukaan import config, llm, stt, tts

    print("Pre-warming Dukaan models")
    print(f"  LLM : {config.LLM_BASE_URL}")
    print(f"  STT : {config.STT_BASE_URL or '(local, in-process)'}")
    print(f"  TTS : {config.TTS_BASE_URL or '(local, in-process)'}")
    print()

    sr = 16000
    silence = np.zeros(sr // 2, dtype=np.float32)  # 0.5 s — loads Whisper, no real speech
    ok = [
        _timed("llm", lambda: llm.complete("namaste", max_tokens=1)),
        _timed("stt", lambda: stt.transcribe((sr, silence))),
        _timed("tts", lambda: tts.synthesize("नमस्ते, दुकान साथी तैयार है।")),
    ]

    print()
    if all(ok):
        print("All warm — safe to start the demo.")
        return 0
    print("Some endpoints did not warm — check the Modal app is deployed with min_containers>=1.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
