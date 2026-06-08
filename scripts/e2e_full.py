"""Comprehensive REAL-MODEL end-to-end check for Dukaan Saathi.

Loads the actual Gemma-4 (via llama-server) and drives ~2 dozen realistic
shopkeeper turns through the REAL session -> agent -> tools -> ops -> two-DB
stack, against the PRE-COMPUTED ``data/`` databases (copied to a temp dir so the
originals are never mutated). It is self-verifying:

* tool ROUTING is asserted from ``TurnResult.tool_calls`` (read intents must never
  hit a write tool; each write must hit the right write tool),
* every READ answer is compared to a value computed directly from ``ops.*``,
* every WRITE is checked by a before/after DB delta (confirm-before-write),
* guardrails (oversell block, SQL guard, cancel no-op), vision (synthetic
  challan), speech (TTS->STT round-trip) and the proactive/festival/dashboard
  paths are all exercised.

Every Hindi reply is logged verbatim so the responses can be eyeballed. Run
inside ``scripts/e2e_full.sbatch`` AFTER llama-server is healthy. Exit code is
non-zero if any HARD check fails (SOFT answer-quality misses are reported, not
fatal — NL formatting/transliteration varies).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# --- point the app at a COPY of the precomputed DBs (originals stay pristine) ---
# Must happen BEFORE importing dukaan.config.
REPO = Path(__file__).resolve().parents[1]
SRC_DATA = REPO / "data"
_TMP = tempfile.mkdtemp(prefix="dukaan_e2e_full_")
for _fn in ("inventory.db", "transactions.db"):
    _src = SRC_DATA / _fn
    if _src.exists():
        shutil.copy2(_src, Path(_TMP) / _fn)
os.environ["DUKAAN_DATA_DIR"] = _TMP
os.environ.setdefault("DUKAAN_CONFIRM_WRITES", "true")  # force confirm-before-write

from dukaan import (  # noqa: E402
    config,
    db,
    llm,
    normalize,
    ops,
    proactive,
    receiving,
    session,
    stt,
    tts,
)

# ------------------------------------------------------------------- result books
HARD_PASS: list[str] = []
HARD_FAIL: list[str] = []
SOFT: list[tuple[str, bool]] = []


def hard(name: str, cond, info: str = "") -> bool:
    cond = bool(cond)
    (HARD_PASS if cond else HARD_FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'} [HARD] {name}  -  {info}", flush=True)
    return cond


def soft(name: str, cond, info: str = "") -> bool:
    cond = bool(cond)
    SOFT.append((name, cond))
    print(f"  {'ok  ' if cond else 'MISS'} [soft] {name}  -  {info}", flush=True)
    return cond


def section(title: str, fn) -> None:
    print(f"\n=== {title} ===", flush=True)
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — record and keep going
        hard(f"{title}:EXCEPTION", False, f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------- small helpers
READ_TOOLS = {"query_database", "get_dashboard", "get_item_detail", "get_customer_dues"}
WRITE_TOOLS = {"add_inventory_tool", "record_sale_tool", "record_purchase_tool",
               "add_udhaar_tool", "record_payment_tool"}


def _digits(s) -> str:
    return re.sub(r"[^\d]", "", str(s or ""))


def num_present(expected, reply: str) -> bool:
    """True if the integer part of ``expected`` appears in ``reply`` ignoring
    commas/spaces (so ₹2,19,692 and 219692 both match)."""
    try:
        e = str(int(round(float(expected))))
    except Exception:
        return False
    return bool(e) and e in _digits(reply)


def name_present(name: str, reply: str) -> bool:
    """Lenient: any distinctive Latin token (>=4 chars) of ``name`` in ``reply``.
    (Hindi replies may transliterate names, so this is a SOFT signal only.)"""
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name or "") if len(t) >= 4]
    rl = (reply or "").lower()
    return any(t.lower() in rl for t in toks) if toks else bool(name) and name.lower() in rl


def routed_read_only(tr) -> bool:
    return not (set(tr.tool_calls or []) & WRITE_TOOLS)


def used_read(tr) -> bool:
    return bool(set(tr.tool_calls or []) & READ_TOOLS)


def turn(text: str, tid: str, image=None):
    tr = session.handle_turn(text=text, image=image, thread_id=tid, tts=False)
    print(f">>> [{tid}] USER: {text!r}", flush=True)
    print(f"    intent={tr.intent_badge}  tools={tr.tool_calls}  "
          f"pending={'Y' if tr.pending_confirmation else 'N'}", flush=True)
    print(f"    REPLY: {(tr.reply_text or '')[:280]!r}", flush=True)
    if tr.error:
        print(f"    ERROR: {tr.error}", flush=True)
    return tr


def qty_of(item_id) -> int:
    r = db.qx("SELECT qty FROM inv.inventory WHERE item_id=?", (item_id,))
    return int(r[0]["qty"]) if r else -1


def row_count(name: str) -> int:
    return db.qx("SELECT COUNT(*) c FROM inv.inventory WHERE name=?", (name,))[0]["c"]


def bal_of(name: str) -> float:
    return float((ops.customer_balance(name) or {}).get("balance", 0.0))


# ============================================================= boot + ground truth
print("[e2e-full] waiting for llama-server ...", flush=True)
if not llm.wait_until_ready(timeout=300):
    print("FAIL  server_ready  -  llama-server never became healthy", flush=True)
    sys.exit(1)
db.init_db(seed=False, reset=False)  # ensure schema/lots on the copied DBs (idempotent)
print(f"[e2e-full] server up. Using precomputed DBs copied to {_TMP}", flush=True)
print(f"[e2e-full] counts: {db.counts()}", flush=True)

GT = {
    "stock": ops.stock_value(),
    "today": ops.today_summary(),
    "udhaar": ops.pending_udhaar(),
    "low": ops.low_stock(),
    "expiring": ops.expiring_lots(),
    "slow": ops.slow_movers(),
}
ALL_SALES = db.qx(
    "SELECT COALESCE(SUM(qty*sale_price),0) rev, COALESCE(SUM(qty),0) units, COUNT(*) n FROM txn.sales"
)[0]
TOP_SELLER = db.qx(
    "SELECT item_name, SUM(qty) q FROM txn.sales GROUP BY item_name ORDER BY q DESC LIMIT 1"
)
INV = db.qx("SELECT item_id, name, category, qty FROM inv.inventory ORDER BY qty DESC")
SALE_ITEM = next((r for r in INV if 12 <= r["qty"] <= 400), INV[0])
RESTOCK_ITEM = next((r for r in INV if r["item_id"] != SALE_ITEM["item_id"] and r["qty"] > 0), SALE_ITEM)
LOW_ITEM = min((r for r in INV if r["qty"] > 0), key=lambda r: r["qty"])
TOP_DEBTOR = GT["udhaar"]["customers"][0] if GT["udhaar"]["customers"] else None
AMB_TOKEN = None
for _tok in ("doodh", "milk", "atta", "dal", "tel", "oil", "biscuit", "namkeen",
             "chai", "tea", "sabun", "masala", "chips"):
    if db.qx("SELECT COUNT(*) c FROM inv.inventory WHERE name LIKE ?", (f"%{_tok}%",))[0]["c"] >= 2:
        AMB_TOKEN = _tok
        break

print(f"[e2e-full] GROUND TRUTH:", flush=True)
print(f"    stock_value: cost={GT['stock']['at_cost']} mrp={GT['stock']['at_mrp']} "
      f"units={GT['stock']['total_units']} items={GT['stock']['item_count']}", flush=True)
print(f"    today: rev={GT['today']['revenue']} sales={GT['today']['num_sales']}  |  "
      f"all-time: rev={ALL_SALES['rev']} units={ALL_SALES['units']} n={ALL_SALES['n']}", flush=True)
print(f"    udhaar: total={GT['udhaar']['total']} customers={GT['udhaar']['count']}  "
      f"top_debtor={(TOP_DEBTOR or {}).get('name')!r}={(TOP_DEBTOR or {}).get('balance')}", flush=True)
print(f"    low_stock={len(GT['low'])} expiring={len(GT['expiring'])} slow={len(GT['slow'])}", flush=True)
print(f"    top_seller(all-time)={TOP_SELLER[0]['item_name']!r}({TOP_SELLER[0]['q']})" if TOP_SELLER else "    top_seller=none", flush=True)
print(f"    PICKS: sale_item={SALE_ITEM['name']!r}({SALE_ITEM['qty']}) "
      f"restock={RESTOCK_ITEM['name']!r}({RESTOCK_ITEM['qty']}) "
      f"low_item={LOW_ITEM['name']!r}({LOW_ITEM['qty']}) amb_token={AMB_TOKEN!r}", flush=True)


# ====================================================================== A. READS
def _read_today():
    tr = turn("aaj kitni bikri hui?", "r_today")
    hard("read.today:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    rev = GT["today"]["revenue"]
    ok = num_present(rev, tr.reply_text) or (
        rev == 0 and ("0" in tr.reply_text or "nahi" in tr.reply_text.lower()
                      or "koi" in tr.reply_text.lower()))
    soft("read.today:answer", ok, f"expected revenue={rev}")


def _read_stockvalue():
    tr = turn("poori dukaan me kitne rupaye ka maal/stock pada hai?", "r_stockval")
    hard("read.stockvalue:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    soft("read.stockvalue:answer",
         num_present(GT["stock"]["at_cost"], tr.reply_text)
         or num_present(GT["stock"]["at_mrp"], tr.reply_text),
         f"expect cost={GT['stock']['at_cost']} or mrp={GT['stock']['at_mrp']}")


def _read_itemstock():
    cur = qty_of(SALE_ITEM["item_id"])
    tr = turn(f"{SALE_ITEM['name']} ka stock kitna bacha hai?", "r_itemstock")
    hard("read.itemstock:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    soft("read.itemstock:answer", num_present(cur, tr.reply_text), f"expected qty={cur}")


def _read_custbal():
    if not TOP_DEBTOR:
        soft("read.custbal:skipped", False, "no debtor in data")
        return
    nm, b = TOP_DEBTOR["name"], TOP_DEBTOR["balance"]
    tr = turn(f"{nm} ka kitna udhaar baaki hai?", "r_custbal")
    hard("read.custbal:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    soft("read.custbal:answer", num_present(b, tr.reply_text), f"expected balance={b}")


def _read_topudhaar():
    tr = turn("sabse zyada udhaar kis customer ka hai?", "r_topudh")
    hard("read.topudhaar:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    if TOP_DEBTOR:
        soft("read.topudhaar:answer",
             name_present(TOP_DEBTOR["name"], tr.reply_text) or num_present(TOP_DEBTOR["balance"], tr.reply_text),
             f"expect {TOP_DEBTOR['name']!r}/{TOP_DEBTOR['balance']}")


def _read_topseller():
    tr = turn("is dukaan me ab tak sabse zyada bikne wala saamaan kaun sa hai?", "r_topsell")
    hard("read.topseller:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    if TOP_SELLER:
        soft("read.topseller:answer", name_present(TOP_SELLER[0]["item_name"], tr.reply_text),
             f"expect {TOP_SELLER[0]['item_name']!r}")


def _read_lowstock():
    tr = turn("kaun se saamaan ka stock kam ho gaya hai?", "r_low")
    hard("read.lowstock:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    if GT["low"]:
        soft("read.lowstock:answer", any(name_present(x["name"], tr.reply_text) for x in GT["low"][:8]),
             f"{len(GT['low'])} low items, e.g. {GT['low'][0]['name']!r}")


def _read_expiring():
    tr = turn("kya koi saamaan jaldi expire hone wala hai?", "r_exp")
    hard("read.expiring:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")
    if GT["expiring"]:
        soft("read.expiring:answer", any(name_present(x["name"], tr.reply_text) for x in GT["expiring"][:8]),
             f"{len(GT['expiring'])} expiring, e.g. {GT['expiring'][0]['name']!r}")


def _read_dashboard():
    tr = turn("aaj ka poora hisaab / dashboard dikhao", "r_dash")
    hard("read.dashboard:routing", routed_read_only(tr) and used_read(tr), f"tools={tr.tool_calls}")


def _diagnostic():
    if not GT["slow"]:
        soft("diagnostic:skipped", False, "no slow movers in data")
        return
    item = GT["slow"][0]["name"]
    tr = turn(f"{item} abhi tak kyun nahi bik raha?", "r_diag")
    hard("diagnostic:read_only", routed_read_only(tr), f"tools={tr.tool_calls}")
    soft("diagnostic:used_tools", used_read(tr) or tr.intent_badge == "diagnostic",
         f"intent={tr.intent_badge} tools={tr.tool_calls}")


# ===================================================== C. WRITE + CONFIRM (delta)
def _write_sale():
    iid, nm = SALE_ITEM["item_id"], SALE_ITEM["name"]
    before = qty_of(iid)
    tr1 = turn(f"{nm} ke 5 packet bik gaye", "w_sale")
    hard("write.sale:routed_record_sale", "record_sale_tool" in (tr1.tool_calls or []),
         f"tools={tr1.tool_calls}")
    hard("write.sale:staged_not_committed",
         tr1.pending_confirmation is not None and qty_of(iid) == before,
         f"pending={tr1.pending_confirmation is not None} qty={qty_of(iid)} (was {before})")
    turn("haan", "w_sale")
    after = qty_of(iid)
    hard("write.sale:committed_delta", after == before - 5, f"{before} -> {after} (expect {before - 5})")


def _write_udhaar():
    cust = "E2E Veratest Singh"
    b0 = bal_of(cust)
    tr1 = turn(f"{cust} ne 100 rupaye ka udhaar liya", "w_udh")
    hard("write.udhaar:routed_add_udhaar", "add_udhaar_tool" in (tr1.tool_calls or []),
         f"tools={tr1.tool_calls}")
    hard("write.udhaar:staged_not_committed",
         tr1.pending_confirmation is not None and bal_of(cust) == b0,
         f"pending={tr1.pending_confirmation is not None} bal={bal_of(cust)} (was {b0})")
    turn("haan", "w_udh")
    b1 = bal_of(cust)
    hard("write.udhaar:committed_delta", abs(b1 - (b0 + 100)) < 0.5, f"{b0} -> {b1} (expect {b0 + 100})")


def _write_payment():
    if not TOP_DEBTOR:
        soft("write.payment:skipped", False, "no debtor to repay")
        return
    cust = TOP_DEBTOR["name"]
    b0 = bal_of(cust)
    tr1 = turn(f"{cust} ne 50 rupaye jama kar diye", "w_pay")
    hard("write.payment:routed_record_payment", "record_payment_tool" in (tr1.tool_calls or []),
         f"tools={tr1.tool_calls}")
    hard("write.payment:staged_not_committed",
         tr1.pending_confirmation is not None and bal_of(cust) == b0,
         f"pending={tr1.pending_confirmation is not None} bal={bal_of(cust)} (was {b0})")
    turn("haan", "w_pay")
    b1 = bal_of(cust)
    hard("write.payment:committed_delta", abs(b1 - (b0 - 50)) < 0.5, f"{b0} -> {b1} (expect {b0 - 50})")


def _write_purchase_merge():
    iid, nm = RESTOCK_ITEM["item_id"], RESTOCK_ITEM["name"]
    before, rc0 = qty_of(iid), row_count(nm)
    tr1 = turn(f"{nm} ke 10 packet supplier se aaye", "w_pur")
    hard("write.purchase:routed_purchase_or_addinv",
         bool({"record_purchase_tool", "add_inventory_tool"} & set(tr1.tool_calls or [])),
         f"tools={tr1.tool_calls}")
    hard("write.purchase:staged_not_committed",
         tr1.pending_confirmation is not None and qty_of(iid) == before,
         f"pending={tr1.pending_confirmation is not None} qty={qty_of(iid)} (was {before})")
    turn("haan", "w_pur")
    after, rc1 = qty_of(iid), row_count(nm)
    hard("write.purchase:committed_delta", after == before + 10, f"{before} -> {after} (expect {before + 10})")
    hard("write.purchase:qty_merge_no_dup_row", rc0 == 1 and rc1 == 1,
         f"inventory rows for {nm!r}: {rc0} -> {rc1} (must stay 1 — merge, not duplicate)")


# =================================================== D/E. CONFIRM-FLOW + GUARDRAILS
def _cancel_path():
    cust = "E2E Cancelme Verma"
    tr1 = turn(f"{cust} ne 70 rupaye ka udhaar liya", "c_cancel")
    hard("cancel:staged", tr1.pending_confirmation is not None,
         f"pending={tr1.pending_confirmation is not None}")
    turn("rehne do", "c_cancel")
    b = bal_of(cust)
    hard("cancel:not_committed", b == 0.0, f"balance after cancel = {b} (expect 0 — nothing written)")


def _oversell_block():
    iid, nm = LOW_ITEM["item_id"], LOW_ITEM["name"]
    before = qty_of(iid)
    tr1 = turn(f"{nm} ke {before + 9999} packet bik gaye", "g_oversell")
    if tr1.pending_confirmation is not None:
        turn("haan", "g_oversell")  # commit must REFUSE (insufficient stock)
    after = qty_of(iid)
    hard("guard.oversell:stock_unchanged_nonneg", after == before and after >= 0,
         f"{nm}: {before} -> {after} (must be unchanged, never negative)")


def _sql_guard():
    hard("guard.sql:delete_blocked", not db.run_select("DELETE FROM inv.inventory")["ok"], "DELETE rejected")
    hard("guard.sql:multi_blocked",
         not db.run_select("SELECT 1; DROP TABLE inventory")["ok"], "multi-statement rejected")
    r = db.run_select("SELECT COUNT(*) n FROM inv.inventory")
    hard("guard.sql:select_ok", r["ok"] and r["rows"], f"SELECT ok rows={r.get('rows')}")


def _ambiguity():
    if not AMB_TOKEN:
        soft("guard.ambiguity:skipped", False, "no ambiguous token in catalog")
        return
    tr = turn(f"{AMB_TOKEN} ke 2 packet bik gaye", "g_amb")
    rl = tr.reply_text or ""
    # "asked to disambiguate" = no write staged AND it either says kaun/कौन or
    # lists >=2 numbered candidates (the model replies in Devanagari or Latin).
    asked = tr.pending_confirmation is None and (
        "kaun" in rl.lower() or "कौन" in rl or len(re.findall(r"\d\s*\)", rl)) >= 2)
    staged_one = tr.pending_confirmation is not None
    soft("guard.ambiguity:sane", asked or staged_one,
         f"token={AMB_TOKEN!r} asked_kaunsa={asked} staged_specific={staged_one}")


# ====================================================================== F. VISION
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
    for i, line in enumerate(["Item                 Qty    Rate",
                              "Parle-G Biscuit       24      5",
                              "Tata Salt 1kg         10     22",
                              "Amul Butter 100g       6     52"]):
        d.text((20, 70 + i * 40), line, fill="black", font=font)
    pc = normalize.parse_challan(img)
    hard("vision.parse_challan_ok", bool(pc.get("ok")) and len(pc.get("lines", [])) >= 1,
         f"{len(pc.get('lines', []))} lines, supplier={pc.get('supplier')!r}")
    st = receiving.stage_receive(image=img)
    hard("vision.stage_receive_ok", bool(st.get("ok")) and len(st.get("items", [])) >= 1,
         f"{len(st.get('items', []))} staged items; msg={str(st.get('message'))[:60]!r}")


# ====================================================================== G. SPEECH
def _speech():
    sr, wav = tts.synthesize("नमस्ते, आज की बिक्री पाँच सौ बीस रुपये रही।")
    n = 0 if wav is None else len(wav)
    hard("speech.tts_nonempty_audio", n > 0, f"sr={sr} samples={n}")
    tr = stt.transcribe((sr, wav))
    hard("speech.stt_roundtrip_ok", bool(getattr(tr, "ok", False)), f"reason={getattr(tr, 'reason', None)}")
    soft("speech.stt_detected_hindi", getattr(tr, "language", None) == "hi",
         f"lang={getattr(tr, 'language', None)} conf={getattr(tr, 'confidence', 0):.2f}")
    soft("speech.stt_nonempty_text", bool((getattr(tr, "text", "") or "").strip()),
         f"text={getattr(tr, 'text', '')[:50]!r}")


# ====================================== H. PROACTIVE / CALENDAR / DASHBOARD
def _proactive():
    mb = session.morning_briefing(tts=False)
    hard("proactive.briefing_nonempty", bool((mb.get("text") or "").strip()),
         f"text={(mb.get('text') or '')[:90]!r}")
    parts = mb.get("parts", {})
    hard("proactive.briefing_has_parts", all(k in parts for k in ("expiry", "udhaar", "festival")),
         f"keys={list(parts)}")

    fests = proactive._load_festivals([2026, 2027])
    _few = ", ".join(f"{f['name']}@{f['date']}" for f in fests[:5])
    hard("proactive.calendar_2026_present",
         len(fests) > 0 and any(f["date"].year == 2026 for f in fests),
         f"{len(fests)} festivals; e.g. {_few}")
    nudge = proactive.festival_nudge()
    fest = nudge.get("festival")
    soft("proactive.next_festival_nudge", bool((nudge.get("message") or "").strip()),
         f"next={(fest or {}).get('name')} in {nudge.get('days_away')}d :: {(nudge.get('message') or '')[:80]!r}")

    snap = session.dashboard_snapshot_struct()
    need = ("stock_value", "today", "udhaar", "expiring", "low_stock", "festival", "server_up")
    hard("dashboard.struct_keys", all(k in snap for k in need), f"keys={list(snap)}")
    hard("dashboard.server_up", snap.get("server_up") is True, f"server_up={snap.get('server_up')}")


# ================================================================= run everything
section("A. READ — today's sales (routing + answer)", _read_today)
section("A. READ — stock value", _read_stockvalue)
section("A. READ — item stock", _read_itemstock)
section("A. READ — customer balance", _read_custbal)
section("A. READ — top debtor", _read_topudhaar)
section("A. READ — top seller (all-time, cross-DB)", _read_topseller)
section("A. READ — low stock", _read_lowstock)
section("A. READ — expiring soon", _read_expiring)
section("A. READ — dashboard summary", _read_dashboard)
section("B. DIAGNOSTIC — why not selling", _diagnostic)
section("C. WRITE — sale (stage->haan->FEFO delta)", _write_sale)
section("C. WRITE — udhaar (stage->haan->+100)", _write_udhaar)
section("C. WRITE — payment (stage->haan->-50)", _write_payment)
section("C. WRITE — purchase/restock (merge, no dup row)", _write_purchase_merge)
section("D. CONFIRM — cancel path (no write)", _cancel_path)
section("E. GUARD — oversell hard-block", _oversell_block)
section("E. GUARD — read-only SQL guard", _sql_guard)
section("E. GUARD — ambiguous name disambiguation", _ambiguity)
section("F. VISION — synthetic challan parse + stage", _vision)
section("G. SPEECH — TTS -> STT round-trip", _speech)
section("H. PROACTIVE — briefing + festival calendar + dashboard", _proactive)

# ===================================================================== summary
print("\n" + "=" * 72, flush=True)
print(f"[e2e-full] HARD: {len(HARD_PASS)} passed, {len(HARD_FAIL)} failed", flush=True)
_sm = sum(1 for _, ok in SOFT if ok)
print(f"[e2e-full] SOFT (answer/quality): {_sm}/{len(SOFT)} matched", flush=True)
if HARD_FAIL:
    print("[e2e-full] HARD FAILURES: " + ", ".join(HARD_FAIL), flush=True)
_miss = [n for n, ok in SOFT if not ok]
if _miss:
    print("[e2e-full] soft misses (review the replies above): " + ", ".join(_miss), flush=True)
print("=" * 72, flush=True)
sys.exit(1 if HARD_FAIL else 0)
