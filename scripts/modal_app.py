"""Dukaan Saathi GPU services on Modal, SPLIT across two GPUs so neither starves:

    serve         (L4)  -> the LLM + vision/OCR  (llama.cpp llama-server)
        GET  /health
        *    /v1/...                    -> OpenAI Chat Completions (+ vision via image_url)
    serve_speech  (T4)  -> STT (faster-whisper) + TTS (Veena)
        GET  /health
        POST /stt   {sr, audio:[...]}   -> {"text","language","confidence","no_speech","ok","reason"}
        POST /tts   {"text": "..."}     -> WAV bytes (audio/wav), header x-sample-rate

Why split: a single L4 (24GB) cannot hold Gemma-12B (`-ngl 99`) + 2x Whisper + Veena
on-GPU at once. Veena gets VRAM-starved — `device_map="auto"` CPU-offloads it (~3 tok/s,
truncated audio) and forcing it onto the GPU OOMs into a *silent* 46-byte WAV. A dedicated
**T4 (16GB)** for speech (Whisper ~4.5GB + Veena 4-bit ~3GB) runs both fully on-GPU, fast +
complete, while the LLM keeps the L4 to itself.

Deploy (one app, two web functions -> two URLs):
    MODAL_PROFILE=projects-ps MIN_CONTAINERS=1 PYTHONPATH="$PWD" modal deploy scripts/modal_app.py
    #   LLM:      https://<ws>--dukaan-llm-serve.modal.run/v1
    #   STT/TTS:  https://<ws>--dukaan-llm-serve-speech.modal.run/{stt,tts}
Point the Space at BOTH (Settings -> Variables/secrets):
    DUKAAN_LLM_BASE_URL = https://<ws>--dukaan-llm-serve.modal.run/v1
    DUKAAN_STT_BASE_URL = https://<ws>--dukaan-llm-serve-speech.modal.run/stt
    DUKAAN_TTS_BASE_URL = https://<ws>--dukaan-llm-serve-speech.modal.run/tts

Veena is GATED -> the speech container needs an HF token (Modal secret "huggingface").
"""
import os
import subprocess

import modal

MIN = 60
LLM_GPU = os.environ.get("MODAL_GPU", "L4")               # LLM + vision/OCR
SPEECH_GPU = os.environ.get("MODAL_SPEECH_GPU", "L4")     # STT + TTS, dedicated. MUST be a
# native-bf16 GPU (Ada/Ampere) — a T4 (Turing) has no bf16, so bf16 Veena errors and 4-bit
# Veena early-stops into truncated audio. A dedicated L4 (Ada, 24GB) runs bf16 Veena complete + fast.
MIN_CONTAINERS = int(os.environ.get("MIN_CONTAINERS", "1"))
CACHE = "/cache"
LLM_REPO = os.environ.get("LLM_REPO", "ggml-org/gemma-4-12B-it-GGUF")
LLM_FILE = os.environ.get("LLM_FILE", "gemma-4-12B-it-Q4_K_M.gguf")
LLM_MMPROJ = os.environ.get("LLM_MMPROJ", "mmproj-gemma-4-12B-it-Q8_0.gguf")
LLM_CTX = os.environ.get("LLM_CTX", "16384")   # deepagents prompt+tools ~9.4k tokens

app = modal.App("dukaan-llm")

image = (
    modal.Image.from_registry("ghcr.io/ggml-org/llama.cpp:server-cuda", add_python="3.12")
    .pip_install(
        "fastapi", "httpx", "huggingface_hub[hf_transfer]",
        "faster-whisper", "transformers>=5.10.1", "torch", "snac",
        "soundfile", "scipy", "numpy<2.2", "uroman",
    )
    .pip_install("bitsandbytes", "accelerate")   # 4-bit Veena
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "DUKAAN_WHISPER_DEVICE": "cuda",
        "DUKAAN_TTS_DEVICE": "cuda",
        # bf16 Veena on the dedicated speech L4 (Ada, native bf16, 24GB): Veena (~6GB) +
        # Whisper (~4.5GB) run fully on-GPU -> fast AND complete. (4-bit quantization makes
        # Veena early-stop into truncated clips; bf16 gives the full utterance.)
        "DUKAAN_VEENA_4BIT": "false",
        "DUKAAN_DATA_DIR": "/tmp/dukaan-data",
    })
    .entrypoint([])
    .add_local_python_source("dukaan")   # reuse dukaan.stt / dukaan.tts — must be LAST
)
hf_cache = modal.Volume.from_name("dukaan-hf-cache", create_if_missing=True)


# ============================================================ LLM + vision (L4)
@app.function(
    image=image,
    gpu=LLM_GPU,
    volumes={CACHE: hf_cache},
    timeout=24 * MIN * MIN,            # 24h max; Modal rolling-replaces at the boundary
    min_containers=MIN_CONTAINERS,
    scaledown_window=10 * MIN,
    secrets=[modal.Secret.from_name("huggingface")],
)
@modal.concurrent(max_inputs=24)
@modal.asgi_app()
def serve():
    """Gemma LLM + vision/OCR via the llama.cpp llama-server on the L4."""
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response
    from huggingface_hub import hf_hub_download

    gguf = hf_hub_download(LLM_REPO, LLM_FILE, local_dir=CACHE)
    mmproj = hf_hub_download(LLM_REPO, LLM_MMPROJ, local_dir=CACHE)
    hf_cache.commit()

    import shutil
    binpath = shutil.which("llama-server")
    if not binpath:
        for cand in ("/app/llama-server", "/usr/local/bin/llama-server",
                     "/usr/bin/llama-server", "/llama-server"):
            if os.path.exists(cand):
                binpath = cand
                break
    binpath = binpath or "llama-server"
    bindir = os.path.dirname(binpath) or "/app"
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = bindir + ":" + env.get("LD_LIBRARY_PATH", "")
    print(f"[modal_app] starting llama-server: {binpath} (LD_LIBRARY_PATH+={bindir})", flush=True)
    subprocess.Popen(
        [binpath, "-m", gguf, "--mmproj", mmproj, "--host", "127.0.0.1",
         "--port", "8080", "-c", LLM_CTX, "-ngl", "99", "--jinja"],
        env=env,
    )

    web = FastAPI()
    llm = httpx.AsyncClient(base_url="http://127.0.0.1:8080", timeout=300.0)

    @web.get("/health")
    async def health():
        try:
            r = await llm.get("/health")
            return JSONResponse({"ok": True, "llm": r.status_code == 200})
        except Exception:
            return JSONResponse({"ok": True, "llm": False})

    @web.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request):
        body = await request.body()
        r = await llm.request(
            request.method, f"/v1/{path}", content=body,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

    return web


# ============================================================ STT + TTS (T4)
@app.function(
    image=image,
    gpu=SPEECH_GPU,
    timeout=24 * MIN * MIN,
    min_containers=MIN_CONTAINERS,
    scaledown_window=10 * MIN,
    secrets=[modal.Secret.from_name("huggingface")],
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def serve_speech():
    """faster-whisper STT + Veena TTS on a dedicated T4 — no llama-server, so both
    models run fully on-GPU (fast + complete) without competing with the LLM."""
    import io
    import threading

    import numpy as np
    import soundfile as sf
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    # Pre-load both speech models at container startup so the first /stt and /tts
    # calls aren't a cold model load (≈27s STT / ≈75s TTS otherwise).
    def _warm():
        try:
            from dukaan import stt as _stt, tts as _tts
            _stt.warmup()
            _tts.warmup()
            print("[modal_app] STT+TTS pre-loaded on the speech GPU", flush=True)
        except Exception as e:  # noqa: BLE001 — warmup is best-effort
            print(f"[modal_app] speech warmup failed: {e}", flush=True)

    threading.Thread(target=_warm, daemon=True).start()

    web = FastAPI()

    @web.get("/health")
    async def health():
        return JSONResponse({"ok": True, "speech": True})

    @web.post("/stt")
    async def stt(request: Request):
        from dukaan import stt as _stt   # model loads lazily / from the warm pre-load
        p = await request.json()
        arr = np.asarray(p.get("audio", []), dtype=np.float32)
        res = _stt.transcribe((int(p.get("sr", 16000)), arr), language=p.get("language"))
        return JSONResponse({
            "text": res.text, "language": res.language, "confidence": res.confidence,
            "no_speech": res.no_speech, "ok": res.ok, "reason": res.reason,
        })

    @web.post("/tts")
    async def tts(request: Request):
        from dukaan import tts as _tts
        p = await request.json()
        sr, wav = _tts.synthesize(p.get("text", ""))
        buf = io.BytesIO()
        sf.write(buf, np.asarray(wav, dtype=np.float32), int(sr), format="WAV")
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav",
                        headers={"x-sample-rate": str(int(sr))})

    return web
