"""Offline verification of the four UI fixes (no live LLM / GPU needed).

Exercises the pure-logic pieces of each change:
  1. language tag prefix (agent._lang_prefix)
  2. OCR image preprocessing (llm._image_to_data_uri downsizes + auto-orients)
  3. robust mic decode (stt.decode_audio_file via PyAV on a webm/opus file)
  4a. composer voice-send (app._parse_composer returns audio for a webm note)
  4b. streaming TTS (tts.synthesize_stream yields per sentence)
  + New Chat / past-chat session logic (app.new_chat / open_chat / chats_html)
"""
from __future__ import annotations

import base64
import io
import sys
import tempfile

import numpy as np

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


# 1) language tag -------------------------------------------------------------
from dukaan import agent

check("lang_prefix hi", agent._lang_prefix("hi") == "[reply:hi] ", agent._lang_prefix("hi"))
check("lang_prefix en", agent._lang_prefix("en") == "[reply:en] ", agent._lang_prefix("en"))
check("lang_prefix default->en", agent._lang_prefix("") == "[reply:en] ")
check("system prompt has tag rule", "[reply:" in agent.DUKAAN_SYSTEM_PROMPT)


# 2) OCR image preprocessing --------------------------------------------------
from PIL import Image

from dukaan import llm

big = Image.new("RGB", (3000, 2000), (200, 30, 30))
uri = llm._image_to_data_uri(big)
check("vision uri is jpeg data-uri", uri.startswith("data:image/jpeg;base64,"))
dec = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
check("vision image downsized to <=1600 edge", max(dec.size) <= 1600, f"{dec.size}")


# 3 + 4a) robust mic decode (webm/opus) --------------------------------------
def _make_webm(path: str, sr: int = 48000, secs: float = 1.0, freq: float = 440.0) -> bool:
    """Write a real webm/opus file with PyAV; return False if the encoder is absent."""
    try:
        import av
    except Exception:
        return False
    for codec, ext_fmt in (("libopus", "webm"), ("libvorbis", "ogg")):
        try:
            t = np.linspace(0, secs, int(sr * secs), endpoint=False)
            tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
            container = av.open(path, mode="w", format=ext_fmt)
            stream = container.add_stream(codec, rate=sr)
            try:
                stream.layout = "mono"
            except Exception:
                pass
            frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = sr
            for pkt in stream.encode(frame):
                container.mux(pkt)
            for pkt in stream.encode(None):
                container.mux(pkt)
            container.close()
            return True
        except Exception:
            continue
    return False


from dukaan import stt

tmp = tempfile.mkdtemp()
webm = f"{tmp}/note.webm"
have_webm = _make_webm(webm)
if have_webm:
    # soundfile alone CANNOT read the webm container...
    sf_fails = False
    try:
        import soundfile as sf
        sf.read(webm, dtype="float32")
    except Exception:
        sf_fails = True
    check("soundfile cannot read webm (so the old path dropped it)", sf_fails)
    # ...but decode_audio_file (PyAV fallback) recovers real samples.
    out = stt.decode_audio_file(webm)
    ok = out is not None and out[1].size > 1000 and float(np.abs(out[1]).mean()) > 1e-3
    check("decode_audio_file decodes webm/opus", ok, f"{None if out is None else (out[0], out[1].shape)}")

    # 4a) the composer parses that voice note into an audio tuple (no silent drop)
    from dukaan import app
    text, image, audio = app._parse_composer({"text": "", "files": [{"path": webm}]})
    check("_parse_composer returns audio for a webm note",
          audio is not None and audio[1].size > 1000, f"audio={'set' if audio is not None else None}")
else:
    # No opus/vorbis encoder in this PyAV build — exercise the PyAV branch on a wav
    # by forcing the soundfile path to fail (proves the resampler code runs).
    import soundfile as sf
    wav = f"{tmp}/note.wav"
    t = np.linspace(0, 1.0, 16000, endpoint=False)
    sf.write(wav, (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000)
    orig = sf.read
    try:
        sf.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced"))
        out = stt.decode_audio_file(wav)
    finally:
        sf.read = orig
    ok = out is not None and out[1].size > 1000
    check("decode_audio_file PyAV branch decodes (webm encoder absent; tested on wav)", ok,
          "NOTE: install a libopus PyAV build to test webm directly")


# 4b) streaming TTS -----------------------------------------------------------
from dukaan import config, tts

prev = config.TTS_BASE_URL
try:
    config.TTS_BASE_URL = "http://fake-tts/tts"
    calls: list[str] = []

    def _fake_remote(text):
        calls.append(text)
        return 24000, np.ones(2400, dtype=np.float32)

    tts._remote_synthesize = _fake_remote  # type: ignore[assignment]

    long_text = ("Yeh ek lamba pehla vakya hai jismein kaafi shabd hain taaki chunk bhar jaaye. "
                 "Aur yeh doosra utna hi lamba vakya hai jo alag chunk banata hai bilkul saaf.")
    expected = tts._veena_chunks(long_text)
    chunks = list(tts.synthesize_stream(long_text))
    check("synthesize_stream yields one chunk per sentence-chunk",
          len(chunks) == len(expected) == len(calls), f"chunks={len(chunks)} expected={len(expected)} calls={len(calls)}")
    check("synthesize_stream chunks carry audio", all(c[1].size > 1 for c in chunks))
    empty = list(tts.synthesize_stream(""))
    check("synthesize_stream empty -> one silence", len(empty) == 1 and empty[0][1].size >= 1)
finally:
    config.TTS_BASE_URL = prev


# New Chat / past chats logic -------------------------------------------------
from dukaan import app

hist = [{"role": "user", "text": "Sharma ji ka kitna baaki?"},
        {"role": "bot", "text": "₹1,115 baaki hai."}]
chats, new_hist, new_tid, _chat, _bar, _conf, _aud = app.new_chat([], hist, "tid-A")
check("new_chat archives the old chat", len(chats) == 1 and chats[0]["id"] == "tid-A")
check("new_chat title = first user msg", chats[0]["title"].startswith("Sharma ji"))
check("new_chat clears history + mints new thread", new_hist == [] and new_tid != "tid-A")
check("chats_html empty -> blank", app.chats_html([]) == "")
bar = app.chats_html(chats)
check("chats_html renders a clickable chip", 'data-chat="tid-A"' in bar and "Sharma ji" in bar)

chats2, hist2, tid2, *_ = app.open_chat("tid-A|999", chats, [], new_tid)
check("open_chat restores the past history", hist2 == hist)
check("open_chat restores the thread_id", tid2 == "tid-A")
check("open_chat removes the reopened chat from the list", all(c["id"] != "tid-A" for c in chats2))


# ---------------------------------------------------------------------------
print("\n" + ("ALL VERIFIES PASSED" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
