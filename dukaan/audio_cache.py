"""Per-message TTS cache: synthesize a reply's audio ONCE, keyed by a stable
message id, and serve it as a WAV file path. Decouples synthesis (background,
toggle-gated) from playback (a tap in the UI), so playback is instant and the
voice is generated with headroom. Uses :func:`dukaan.session.speak` (the seam) so
every utterance is the one pinned app voice. Never raises to the caller.
"""
from __future__ import annotations

import collections
import logging
import os
import tempfile
import threading

import numpy as np
import soundfile as sf

from dukaan import session

log = logging.getLogger("dukaan.audio_cache")

_DIR = tempfile.mkdtemp(prefix="dukaan-tts-")
_LOCK = threading.Lock()
# mid -> {"status": "pending"|"ready"|"error", "path": str|None, "event": Event}
_CACHE: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_MAX = 64
_SILENCE_PATH: str | None = None


def _silence_path() -> str:
    """A tiny inaudible WAV, written once, returned on any synth failure so the UI
    always gets a playable file (the spinner never gets stuck)."""
    global _SILENCE_PATH
    if _SILENCE_PATH and os.path.exists(_SILENCE_PATH):
        return _SILENCE_PATH
    p = os.path.join(_DIR, "_silence.wav")
    sf.write(p, np.zeros(240, dtype=np.float32), 24000, format="WAV")
    _SILENCE_PATH = p
    return p


def _evict_locked() -> None:
    while len(_CACHE) > _MAX:
        _, old = _CACHE.popitem(last=False)
        p = old.get("path")
        if p and p != _SILENCE_PATH and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def get(mid: str) -> dict | None:
    """Snapshot of a cache entry (without the internal Event), or None."""
    with _LOCK:
        e = _CACHE.get(mid)
        return {"status": e["status"], "path": e["path"]} if e else None


def _begin(mid: str) -> tuple[bool, threading.Event]:
    """Atomically claim ``mid``. Returns (we_own_synthesis, done_event)."""
    with _LOCK:
        e = _CACHE.get(mid)
        if e is not None:
            _CACHE.move_to_end(mid)
            return False, e["event"]
        ev = threading.Event()
        _CACHE[mid] = {"status": "pending", "path": None, "event": ev}
        _evict_locked()
        return True, ev


def _finish(mid: str, status: str, path: str | None) -> None:
    with _LOCK:
        e = _CACHE.get(mid)
        if e is None:
            e = {"status": status, "path": path, "event": threading.Event()}
            _CACHE[mid] = e
        e["status"], e["path"] = status, path
        e["event"].set()


def _synthesize(mid: str, text: str) -> None:
    try:
        res = session.speak(text)  # (sr, ndarray) or None — never raises
        if not res or np.asarray(res[1]).size <= 1:
            _finish(mid, "error", _silence_path())
            return
        sr, wav = res
        path = os.path.join(_DIR, f"{mid}.wav")
        sf.write(path, np.asarray(wav, dtype=np.float32), int(sr), format="WAV")
        _finish(mid, "ready", path)
    except Exception as exc:  # noqa: BLE001 — voice path never breaks the UI
        log.warning("audio_cache synth failed (%s)", exc)
        _finish(mid, "error", _silence_path())


def prepare(mid: str, text: str) -> None:
    """Synthesize ``mid`` if no one else has. Idempotent, blocking."""
    own, _ = _begin(mid)
    if own:
        _synthesize(mid, text)


def prepare_async(mid: str, text: str) -> None:
    """Kick off synthesis on a daemon thread (the pre-gen 'headroom')."""
    threading.Thread(target=prepare, args=(mid, text), daemon=True).start()


def ensure_ready(mid: str, text: str, timeout: float = 120.0) -> str | None:
    """Return a playable WAV path for ``mid``: synthesize now if we own it, else
    wait for the in-flight job. Returns the silence path on failure; only None if
    waiting times out."""
    own, ev = _begin(mid)
    if own:
        _synthesize(mid, text)
    else:
        ev.wait(timeout)
    e = get(mid)
    return e["path"] if e else None
