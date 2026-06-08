"""GPU end-to-end check — exercises the REAL stack the headless tests mock.

Run inside the Slurm job (scripts/e2e_gpu.sbatch) AFTER llama-server is up:
real Gemma-4 agent (tool-calling + the new confirm/staging flow), real Gemma
vision (challan OCR -> structured lines), and a TTS->STT round-trip (incl. the
Hindi 2nd pass). Uses a throwaway DB so nothing real is touched. Exit code is
non-zero if any check fails.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Fresh throwaway DB — must be set BEFORE importing dukaan.config.
os.environ.setdefault("DUKAAN_DATA_DIR", tempfile.mkdtemp(prefix="dukaan_e2e_"))

from dukaan import agent, db, llm, normalize, ops, receiving, session, stt, tts  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, info: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  -  {info}", flush=True)


def section(name, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — record, keep going
        check(name, False, f"EXCEPTION {type(exc).__name__}: {exc}")


def _has_devanagari(s: str) -> bool:
    return any("ऀ" <= c <= "ॿ" for c in s)


print("[e2e] waiting for llama-server ...", flush=True)
if not llm.wait_until_ready(timeout=300):
    print("FAIL  server_ready  -  llama-server never became healthy")
    sys.exit(1)
db.init_db(reset=True, seed=True)
print("[e2e] server up, DB seeded; running checks\n", flush=True)


# 1) Real agent LOOKUP turn -> non-empty Devanagari reply
def _lookup():
    r = agent.run_agent("aaj kitni bikri hui?", thread_id="e2e_lookup")
    reply = (r.get("reply") or "").strip()
    check("agent_lookup_nonempty", bool(reply), reply[:70])
    check("agent_reply_devanagari", _has_devanagari(reply), "Hindi script")
    check("agent_intent_badge", r.get("intent") in {"lookup", "diagnostic", "chat", "write"},
          f"intent={r.get('intent')}")


section("agent_lookup", _lookup)


# 2) Real agent WRITE -> stages (confirm-before-write) -> confirm commits
def _write_confirm():
    name = "E2E Tester Singh"
    before = (ops.customer_balance(name) or {}).get("balance", 0.0)
    r = agent.run_agent(f"{name} ne 100 rupaye ka udhaar liya", thread_id="e2e_write")
    print(f"   [write turn] tool_calls={r.get('tool_calls')} reply={(r.get('reply') or '')[:60]!r}", flush=True)
    pend = r.get("pending")
    staged_unwritten = pend is not None and (ops.customer_balance(name) or {}).get("balance", 0.0) == before
    check("write_staged_not_committed", staged_unwritten,
          f"pending={'set' if pend else 'none'}, balance still {before}")
    session.confirm_pending("haan", thread_id="e2e_write", tts=False)
    after = (ops.customer_balance(name) or {}).get("balance", 0.0)
    check("confirm_commits_udhaar", after >= before + 90, f"{before} -> {after}")


section("agent_write_confirm", _write_confirm)


# 3) Real vision -> challan photo parsed into structured lines
def _vision():
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        fbig = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = fbig = ImageFont.load_default()
    img = Image.new("RGB", (760, 340), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 16), "RAJ TRADERS — CHALLAN / INVOICE", fill="black", font=fbig)
    rows = ["Item                 Qty    Rate",
            "Parle-G Biscuit       24      5",
            "Tata Salt 1kg         10     22",
            "Amul Butter 100g       6     52"]
    for i, line in enumerate(rows):
        d.text((20, 70 + i * 40), line, fill="black", font=font)
    pc = normalize.parse_challan(img)
    check("vision_parse_challan_ok", bool(pc.get("ok")) and len(pc.get("lines", [])) >= 1,
          f"{len(pc.get('lines', []))} lines, supplier={pc.get('supplier')}")
    st = receiving.stage_receive(image=img)
    check("receiving_stage_from_image", bool(st.get("ok")) and len(st.get("items", [])) >= 1,
          f"{len(st.get('items', []))} staged items; msg={str(st.get('message'))[:50]}")


section("vision_challan", _vision)


# 4) STT round-trip: TTS(Hindi) -> STT auto-detect (exercises the Hindi 2nd pass)
def _stt_roundtrip():
    sr, wav = tts.synthesize("नमस्ते, आज की बिक्री पाँच सौ रुपये रही।")
    tr = stt.transcribe((sr, wav))
    check("stt_roundtrip_ok", bool(tr.ok), f"reason={tr.reason}")
    check("stt_detected_hindi", tr.language == "hi",
          f"lang={tr.language} conf={tr.confidence:.2f} text={tr.text[:40]!r}")


section("stt_roundtrip", _stt_roundtrip)


print(f"\n[e2e] RESULT: {len(PASSED)} passed, {len(FAILED)} failed", flush=True)
if FAILED:
    print("[e2e] FAILED:", ", ".join(FAILED), flush=True)
sys.exit(1 if FAILED else 0)
