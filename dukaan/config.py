"""Central configuration for Dukaan Saathi.

Every value can be overridden with an environment variable (optionally via a
`.env` file at the project root). Defaults are tuned for the vitallab2 cluster:
one GPU, Gemma-4-12B Q8_0 served by llama.cpp at :8080, Gradio at :7860.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # python-dotenv optional at import time
    pass


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _flag(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- paths
BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = Path(_env("DUKAAN_DATA_DIR", str(BASE_DIR / "data")))
# Two separate databases: catalog/stock vs transactions/khata.
INVENTORY_DB_PATH = Path(_env("DUKAAN_INVENTORY_DB", str(DATA_DIR / "inventory.db")))
TRANSACTIONS_DB_PATH = Path(_env("DUKAAN_TRANSACTIONS_DB", str(DATA_DIR / "transactions.db")))

# --------------------------------------------------------------- LLM (llama-server)
# OpenAI-compatible endpoint exposed by `llama-server`.
LLM_BASE_URL = _env("DUKAAN_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
LLM_API_KEY = _env("DUKAAN_LLM_API_KEY", "not-needed")  # any non-empty string
LLM_MODEL = _env("DUKAAN_LLM_MODEL", "gemma-4-12b")
LLM_TEMPERATURE = float(_env("DUKAAN_LLM_TEMPERATURE", "0.0"))
LLM_REQUEST_TIMEOUT = float(_env("DUKAAN_LLM_REQUEST_TIMEOUT", "120"))
# Gemma-4 has a native "thinking" mode that is slow and can swallow the entire
# answer into reasoning tokens (empty content). We disable it for fast, direct
# replies and reliable tool calls — the deep-agent loop supplies the reasoning.
LLM_ENABLE_THINKING = _flag("DUKAAN_LLM_ENABLE_THINKING", False)
# deepagents / LangGraph step budget (default 25 is too low for multi-tool turns).
AGENT_RECURSION_LIMIT = int(_env("DUKAAN_AGENT_RECURSION_LIMIT", "60"))

# ------------------------------------------------------ llama-server launch params
# (used by scripts/serve_llm.sh and scripts/run.sbatch)
GEMMA_GGUF = Path(
    _env("DUKAAN_GEMMA_GGUF", str(MODELS_DIR / "gemma4" / "gemma-4-12B-it-Q8_0.gguf"))
)
GEMMA_MMPROJ = Path(
    _env("DUKAAN_GEMMA_MMPROJ", str(MODELS_DIR / "gemma4" / "mmproj-gemma-4-12B-it-Q8_0.gguf"))
)
LLM_HOST = _env("DUKAAN_LLM_HOST", "127.0.0.1")
LLM_PORT = int(_env("DUKAAN_LLM_PORT", "8080"))
LLM_CTX = int(_env("DUKAAN_LLM_CTX", "32768"))
LLM_NGL = int(_env("DUKAAN_LLM_NGL", "99"))  # GPU layers (99 = fully offloaded)

# ------------------------------------------------------------------ STT (whisper)
WHISPER_MODEL = _env("DUKAAN_WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = _env("DUKAAN_WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = _env("DUKAAN_WHISPER_COMPUTE", "float16")
# "" => auto-detect language (multilingual input). Set e.g. "hi" to force one.
STT_LANGUAGE = _env("DUKAAN_STT_LANGUAGE", "")
# Optional Hindi-optimized 2nd pass: when large-v3 detects Hindi with high
# confidence, re-transcribe with this faster-whisper (CTranslate2) fine-tune for
# much lower rural/Hinglish WER. "" disables it. Apache-2.0, not gated.
STT_HINDI_MODEL = _env(
    "DUKAAN_STT_HINDI_MODEL", "digikar/vasista22-whisper-hindi-large-v2-ct2-int8"
)
STT_HINDI_THRESHOLD = float(_env("DUKAAN_STT_HINDI_THRESHOLD", "0.80"))
# Below these the transcript is treated as unusable -> we ask the user to repeat
# (never feed empty/garbage to the agent).
STT_MIN_CONFIDENCE = float(_env("DUKAAN_STT_MIN_CONFIDENCE", "0.55"))
STT_MAX_NOSPEECH = float(_env("DUKAAN_STT_MAX_NOSPEECH", "0.60"))

# ------------------------------------------------------------------------- TTS
# "mms"    -> facebook/mms-tts-hin  (open, tiny, fast — default, works out of the box)
# "parler" -> ai4bharat/indic-parler-tts (most natural Hindi, but a GATED HF repo:
#             needs access approval + `huggingface-cli login`, and `pip install
#             git+https://github.com/huggingface/parler-tts.git`). tts.py falls back
#             to "mms" automatically if the parler model/package is unavailable.
TTS_ENGINE = _env("DUKAAN_TTS_ENGINE", "mms")
TTS_DEVICE = _env("DUKAAN_TTS_DEVICE", "cuda")
MMS_MODEL = _env("DUKAAN_MMS_MODEL", "facebook/mms-tts-hin")
PARLER_MODEL = _env("DUKAAN_PARLER_MODEL", "ai4bharat/indic-parler-tts")
PARLER_DESCRIPTION = _env(
    "DUKAAN_PARLER_DESC",
    "Rohit speaks in a clear, warm and friendly voice at a natural pace, "
    "with very clean audio and no background noise.",
)

# ------------------------------------------------------------------------ Gradio
GRADIO_HOST = _env("DUKAAN_GRADIO_HOST", "0.0.0.0")
GRADIO_PORT = int(_env("DUKAAN_GRADIO_PORT", "7860"))
GRADIO_SHARE = _flag("DUKAAN_GRADIO_SHARE", False)

# ---------------------------------------------------------------- business rules
CURRENCY = "₹"
EXPIRY_WARN_DAYS = int(_env("DUKAAN_EXPIRY_WARN_DAYS", "15"))
LOW_STOCK_THRESHOLD = int(_env("DUKAAN_LOW_STOCK_THRESHOLD", "10"))
UDHAAR_OVERDUE_DAYS = int(_env("DUKAAN_UDHAAR_OVERDUE_DAYS", "7"))
FESTIVAL_LOOKAHEAD_DAYS = int(_env("DUKAAN_FESTIVAL_LOOKAHEAD_DAYS", "30"))
SLOW_MOVER_DAYS = int(_env("DUKAAN_SLOW_MOVER_DAYS", "21"))

# ------------------------------------------------------- item resolution (merge)
# Scores from dukaan.resolve (0..100). >= ACCEPT => same item (merge its qty);
# FLOOR..ACCEPT => ambiguous (ask "kaun sa?"); < FLOOR => genuinely new item.
RESOLVE_ACCEPT = float(_env("DUKAAN_RESOLVE_ACCEPT", "92"))
RESOLVE_FLOOR = float(_env("DUKAAN_RESOLVE_FLOOR", "70"))

# --------------------------------------------------------- writes & language seam
# Stage writes and require an explicit haan/nahi before committing (on-camera safe).
CONFIRM_WRITES = _flag("DUKAAN_CONFIRM_WRITES", True)
# Owner's preferred language (captured during onboarding). Replies stay
# Hindi/Hinglish for now; OWNER_LANG + REPLY_LANG_MODE are the seam for future
# multilingual replies ("same_as_user") without reworking callers.
OWNER_LANG = _env("DUKAAN_OWNER_LANG", "hi")
REPLY_LANG_MODE = _env("DUKAAN_REPLY_LANG_MODE", "hindi_only")  # hindi_only | same_as_user

# Indian festival calendar overrides (adds Karwa Chauth, fixes Eid date, and
# carries the per-festival kirana stock hints). Layered on top of `holidays.India`.
FESTIVALS_OVERRIDES_PATH = Path(
    _env("DUKAAN_FESTIVALS_OVERRIDES",
         str(BASE_DIR / "dukaan" / "data" / "festival_overrides.json"))
)

# Ensure runtime dirs exist.
DATA_DIR.mkdir(parents=True, exist_ok=True)
