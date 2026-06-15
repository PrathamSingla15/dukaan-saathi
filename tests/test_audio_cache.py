"""Unit tests for dukaan.audio_cache — the per-message TTS cache.

session.speak is monkeypatched so these run CPU-only with no model.
"""
import os

import numpy as np
import soundfile as sf

from dukaan import audio_cache


def _fake_speak(text):
    # 0.05s of nonzero audio at 24k so it writes a real, readable WAV
    return (24000, np.full(1200, 0.01, dtype=np.float32))


def test_ensure_ready_writes_a_playable_wav(monkeypatch):
    monkeypatch.setattr(audio_cache.session, "speak", _fake_speak)
    path = audio_cache.ensure_ready("mid-A", "Namaste bhai", timeout=5)
    assert path and os.path.exists(path)
    wav, sr = sf.read(path, dtype="float32")
    assert sr == 24000 and wav.size > 1
    assert audio_cache.get("mid-A")["status"] == "ready"


def test_ensure_ready_is_idempotent_same_path(monkeypatch):
    monkeypatch.setattr(audio_cache.session, "speak", _fake_speak)
    p1 = audio_cache.ensure_ready("mid-B", "x", timeout=5)
    p2 = audio_cache.ensure_ready("mid-B", "x", timeout=5)
    assert p1 == p2


def test_failure_returns_silence_path_not_none(monkeypatch):
    monkeypatch.setattr(audio_cache.session, "speak", lambda t: None)
    path = audio_cache.ensure_ready("mid-C", "x", timeout=5)
    assert path and os.path.exists(path)  # silence fallback → spinner never sticks


def test_prepare_async_then_ready(monkeypatch):
    monkeypatch.setattr(audio_cache.session, "speak", _fake_speak)
    audio_cache.prepare_async("mid-D", "x")
    path = audio_cache.ensure_ready("mid-D", "x", timeout=5)  # waits on the same job
    assert path and os.path.exists(path)


def test_eviction_caps_entries(monkeypatch):
    monkeypatch.setattr(audio_cache.session, "speak", _fake_speak)
    monkeypatch.setattr(audio_cache, "_MAX", 3)
    for i in range(6):
        audio_cache.ensure_ready(f"e{i}", "x", timeout=5)
    assert len(audio_cache._CACHE) <= 3
