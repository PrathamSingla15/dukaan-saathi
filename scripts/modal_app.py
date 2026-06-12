"""ALL Dukaan Saathi GPU models on ONE Modal GPU (L4): Gemma LLM + vision (llama.cpp),
faster-whisper STT, and Veena TTS — exposed as a single FastAPI app so the HF Space
can run on free CPU and call this for everything (1 warm GPU = 1x credit cost).

    GET  /health                  -> readiness (also satisfies dukaan.llm.health)
    *    /v1/...                   -> proxied to the in-container llama-server (OpenAI API)
    POST /stt   {sr, audio:[...]}  -> {"text","language","confidence","no_speech","ok","reason"}
    POST /tts   {"text": "..."}    -> WAV bytes (audio/wav), header x-sample-rate

Deploy (reuses app 'dukaan-llm', so the URL stays the same as the LLM-only one):
    modal deploy scripts/modal_app.py
    #   LLM:  https://<workspace>--dukaan-llm-serve.modal.run/v1
    #   STT:  https://<workspace>--dukaan-llm-serve.modal.run/stt
    #   TTS:  https://<workspace>--dukaan-llm-serve.modal.run/tts

Veena is GATED → the container needs an HF token. Create a Modal secret first:
    modal secret create huggingface HF_TOKEN=hf_xxxxxxxx   (token with gated-read access)

Reuses dukaan.stt / dukaan.tts directly (no logic duplicated). The same code runs in
the Space too: there STT/TTS route to this endpoint via DUKAAN_STT_BASE_URL /
DUKAAN_TTS_BASE_URL; here those are unset, so the models run locally on the GPU.
"""
import os
import subprocess

import modal

MIN = 60
GPU = os.environ.get("MODAL_GPU", "L4")           # 24 GB: Gemma Q4 ~7 + Whisper ~3 + Veena ~6
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
    .pip_install("bitsandbytes", "accelerate")   # 4-bit Veena (separate layer keeps the big one cached)
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "DUKAAN_WHISPER_DEVICE": "cuda",
        "DUKAAN_TTS_DEVICE": "cuda",
        "DUKAAN_VEENA_4BIT": "true",    # 4-bit Veena (~3GB) → headroom for the larger LLM KV cache
        "DUKAAN_DATA_DIR": "/tmp/dukaan-data",
    })
    .entrypoint([])
    .add_local_python_source("dukaan")   # reuse dukaan.stt / dukaan.tts — must be the LAST image step
)
hf_cache = modal.Volume.from_name("dukaan-hf-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    volumes={CACHE: hf_cache},
    timeout=24 * MIN * MIN,            # 24h max; Modal rolling-replaces at the boundary
    min_containers=MIN_CONTAINERS,     # keep 1 warm → continuous
    scaledown_window=10 * MIN,
    secrets=[modal.Secret.from_name("huggingface")],   # HF_TOKEN for gated Veena
)
@modal.concurrent(max_inputs=24)
@modal.asgi_app()
def serve():
    import io
    import httpx
    import numpy as np
    import soundfile as sf
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response
    from huggingface_hub import hf_hub_download

    # --- start the llama-server sidecar (LLM + vision OCR) on localhost:8080 ---
    gguf = hf_hub_download(LLM_REPO, LLM_FILE, local_dir=CACHE)
    mmproj = hf_hub_download(LLM_REPO, LLM_MMPROJ, local_dir=CACHE)
    hf_cache.commit()

    # The official llama.cpp image keeps the binary at /app (NOT on PATH), so find
    # it robustly and put its dir on LD_LIBRARY_PATH (it links sibling .so files).
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

    # Pre-load Whisper (STT) + Veena (TTS) in the background at container startup so
    # the FIRST /stt and /tts calls aren't a cold model load — that lazy load is the
    # main source of the "voice responds too late" lag. With min_containers>=1 the
    # warm container then keeps all three models (llama-server, Whisper, Veena)
    # resident in VRAM, so steady-state voice turns stay snappy.
    import threading

    def _warm_speech():
        try:
            from dukaan import stt as _stt, tts as _tts
            _stt.warmup()
            _tts.warmup()
            print("[modal_app] STT+TTS pre-loaded", flush=True)
        except Exception as e:  # noqa: BLE001 — warmup is best-effort
            print(f"[modal_app] speech warmup failed: {e}", flush=True)

    threading.Thread(target=_warm_speech, daemon=True).start()

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

    @web.post("/stt")
    async def stt(request: Request):
        from dukaan import stt as _stt   # imported here so model loads lazily on first call
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
