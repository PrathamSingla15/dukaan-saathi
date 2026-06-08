"""LangChain tools for the Dukaan Saathi deepagents agent.

Thin wrappers over :mod:`dukaan.ops` (business + analytics) and the hardened
read-only SELECT guard in :mod:`dukaan.db`. Each tool takes SIMPLE typed args
(``str`` / ``int`` / ``float``) with ``""`` / ``0`` sentinels instead of
``Optional`` — the small Gemma model fills positional/keyword args far more
reliably that way — and returns a CONCISE string the agent can speak back.

Docstrings use ``parse_docstring=True`` so the ``Args:`` section becomes the
tool's JSON schema; keep them short and Hindi-aware for better grounding.

No network / model load happens at import time — everything is lazy inside the
business layer (``ops`` only touches SQLite).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from dukaan import config, db, i18n, ops, staging


# ============================================================ formatting helpers
def _clean(s: str) -> str | None:
    """Empty/whitespace string -> None (so ops can apply its own default)."""
    s = (s or "").strip()
    return s or None


def _num(x: float) -> float | None:
    """0 / falsy -> None (sentinel for "not provided"), else float(x)."""
    return float(x) if x else None


def _rows_to_text(rows: list[dict], cols: list[str], limit: int = 30) -> str:
    """Render SELECT rows as compact text the model can read cheaply.

    Single scalar -> just the value. A handful of rows -> JSON lines (one row
    per line). Caps at ``limit`` rows and appends a ``…(+N more)`` marker.
    """
    if not rows:
        return "(0 rows)"
    if len(rows) == 1 and len(cols) == 1:
        return f"{cols[0]} = {rows[0][cols[0]]}"
    shown = rows[:limit]
    lines = [json.dumps(r, ensure_ascii=False, default=str) for r in shown]
    out = "\n".join(lines)
    extra = len(rows) - len(shown)
    if extra > 0:
        out += f"\n…(+{extra} more rows)"
    return out


def _money(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return f"{config.CURRENCY}0"
    return f"{config.CURRENCY}{v:,.0f}" if v.is_integer() else f"{config.CURRENCY}{v:,.2f}"


# ==================================================================== write tools
# Every write tool funnels through one of two paths: when config.CONFIRM_WRITES is
# True we STAGE the op (Hindi preview + "haan/nahi" prompt) instead of touching the
# DB; only confirm_pending_tool commits it. When False we call ops.<fn> directly
# (today's behaviour). Disambiguation (R3) runs BEFORE either path so we never
# stage / write an ambiguous or missing entity.
def _stage_or_write(kind: str, kwargs: dict, fn, preview_hi: str) -> str:
    """Stage ``kind`` for confirm-then-write, or call ``fn(**kwargs)`` directly.

    ``kwargs`` MUST be exactly what ``fn`` (the matching ``ops.<kind>``) accepts so
    that ``staging.commit_pending`` can later splat them back in unchanged.
    """
    if config.CONFIRM_WRITES:
        staging.stage_op(staging.current_thread(), kind, kwargs, preview_hi)
        return preview_hi + " — likh dun? (haan/nahi)"
    return str(fn(**kwargs).get("message", "Ho gaya."))


@tool(parse_docstring=True)
def add_inventory_tool(name: str, qty: int, mrp: float = 0, purchase_price: float = 0,
                       category: str = "", expiry_date: str = "") -> str:
    """Stock add karein (नया item बनाएं या मौजूदा का stock बढ़ाएं / restock).

    Use for "X aaye / aa gaye / stock badhao". Item already ho to qty add hoti
    hai, warna naya item banta hai.

    Args:
        name: Item ka naam, e.g. "Parle-G Biscuit", "Amul Butter 100g".
        qty: Kitne units add karne hain (poora number).
        mrp: Maximum Retail Price prati unit (₹). Na pata ho to 0 chhod dein.
        purchase_price: Khareed daam prati unit (₹). Na pata ho to 0.
        category: Category, e.g. "Dairy", "Snacks". Na pata ho to khaali.
        expiry_date: Expiry 'YYYY-MM-DD'. Na ho to khaali.
    """
    # No disambiguation: creating a brand-new item here is valid.
    kwargs = dict(
        name=name, qty=int(qty), mrp=_num(mrp), purchase_price=_num(purchase_price),
        category=_clean(category), expiry_date=_clean(expiry_date),
    )
    preview = f"{int(qty)} × {name} stock me add"
    return _stage_or_write("add_inventory", kwargs, ops.add_inventory, preview)


@tool(parse_docstring=True)
def record_sale_tool(item_name: str, qty: int, sale_price: float = 0,
                     customer_name: str = "") -> str:
    """Bikri (sale) record karein aur stock kam karein.

    Use for "X bika / becha / sell hua". Stock me se qty ghata di jaati hai;
    agar customer diya ho to uske naam se jodi jaati hai.

    Args:
        item_name: Bika hua item ka naam.
        qty: Kitne units bike (poora number).
        sale_price: Prati unit bikri daam (₹). Na diya ho to 0 — item ka MRP use hoga.
        customer_name: Grahak ka naam (optional). Walk-in ho to khaali.
    """
    # R3: an item must exist to be sold — disambiguate before staging/writing.
    res = ops.resolve_item(item_name)
    if res["status"] == "ambiguous":
        return i18n.clarify_entity("item", [c["name"] for c in res["candidates"]])
    if res["status"] == "none":
        return f"'{item_name}' item nahi mila — sahi naam boliye."
    name = (res.get("item") or {}).get("name", item_name)
    kwargs = dict(
        item_name=item_name, qty=int(qty), sale_price=_num(sale_price),
        customer_name=_clean(customer_name),
    )
    preview = f"{int(qty)} × {name} bika"
    return _stage_or_write("record_sale", kwargs, ops.record_sale, preview)


@tool(parse_docstring=True)
def record_purchase_tool(item_name: str, qty: int, cost: float = 0, supplier: str = "",
                         purchase_price: float = 0) -> str:
    """Supplier se aaya maal (purchase / restock) record karein.

    Stock badhta hai aur purchase log hoti hai. `cost` = poore batch ka total
    daam; `purchase_price` = prati unit. Naya item ho to bhi ban jaata hai.

    Args:
        item_name: Item ka naam jo aaya.
        qty: Kitne units aaye (poora number).
        cost: Poore batch ka total daam (₹). Na pata ho to 0.
        supplier: Supplier / distributor ka naam. Na ho to khaali.
        purchase_price: Prati unit khareed daam (₹). Na pata ho to 0.
    """
    # No disambiguation: a purchase may introduce a brand-new item.
    kwargs = dict(
        item_name=item_name, qty=int(qty), cost=_num(cost), supplier=_clean(supplier),
        purchase_price=_num(purchase_price),
    )
    preview = f"{int(qty)} × {item_name} aaya (purchase)"
    return _stage_or_write("record_purchase", kwargs, ops.record_purchase, preview)


@tool(parse_docstring=True)
def add_udhaar_tool(customer_name: str, amount: float, items: str = "",
                    due_date: str = "") -> str:
    """Grahak ke khaate me udhaar (credit) jodein.

    Use for "X ne Y ka udhaar liya / X ke khaate me likho". Grahak naya ho to
    automatic ban jaata hai.

    Args:
        customer_name: Grahak ka naam, e.g. "Sharma ji".
        amount: Udhaar ki rakam (₹).
        items: Kya saamaan liya (optional), e.g. "Atta, Tel".
        due_date: Wapas dene ki taarikh 'YYYY-MM-DD' (optional).
    """
    # R3: disambiguate an existing customer (a brand-new one is fine -> stays).
    res = ops.resolve_customer(customer_name)
    if res["status"] == "ambiguous":
        return i18n.clarify_entity("customer", [c["name"] for c in res["candidates"]])
    name = (res.get("customer") or {}).get("name", customer_name)
    kwargs = dict(customer_name=customer_name, amount=float(amount),
                  items=_clean(items), due_date=_clean(due_date))
    preview = f"{name} ke khate me ₹{float(amount):,.0f} udhaar"
    return _stage_or_write("add_udhaar", kwargs, ops.add_udhaar, preview)


@tool(parse_docstring=True)
def record_payment_tool(customer_name: str, amount: float) -> str:
    """Grahak dwara udhaar ki wapsi (payment) record karein.

    Use for "X ne paise jama kiye / chuka diye". Baaki balance ghat jaata hai.

    Args:
        customer_name: Grahak ka naam jisne paise diye.
        amount: Jama ki gayi rakam (₹).
    """
    # R3: a payment needs an existing payer — disambiguate / reject missing.
    res = ops.resolve_customer(customer_name)
    if res["status"] == "ambiguous":
        return i18n.clarify_entity("customer", [c["name"] for c in res["candidates"]])
    if res["status"] == "none":
        return f"'{customer_name}' customer nahi mila — sahi naam boliye."
    name = (res.get("customer") or {}).get("name", customer_name)
    kwargs = dict(customer_name=customer_name, amount=float(amount))
    preview = f"{name} ne ₹{float(amount):,.0f} jama kiye"
    return _stage_or_write("record_payment", kwargs, ops.record_payment, preview)


# ------------------------------------------------------------------ confirm pending
# Decision-word sets for the confirm gate (Hinglish, lowercased before match).
_YES_WORDS = frozenset({"haan", "haa", "ha", "yes", "ok", "okay", "theek",
                        "thik", "sahi", "kar", "karo", "kardo", "y"})
_NO_WORDS = frozenset({"nahi", "na", "no", "cancel", "mat", "rehne", "rahne",
                       "ruko", "ruk", "n"})


@tool(parse_docstring=True)
def confirm_pending_tool(decision: str) -> str:
    """Pending write ko confirm ya cancel karein (haan/nahi).

    Use jab grahak staged sale/udhaar/stock ke baad "haan/theek/kar do" ya
    "nahi/rehne do" kahe. Haan par DB me likh diya jaata hai; nahi par chhod diya.

    Args:
        decision: Grahak ka jawaab — "haan", "nahi", "theek hai", "kar do", etc.
    """
    text = (decision or "").strip().lower()
    # Hedges ("pata nahi" = don't know, "shayad" = maybe) embed the word "nahi"
    # but are NOT a refusal — fall through to a re-ask rather than cancelling.
    if any(h in text for h in ("pata nahi", "nahi pata", "shayad", "maybe")):
        return "Haan ya nahi boliye."
    words = set(text.replace("-", " ").split())
    if words & _YES_WORDS or any(p in text for p in ("kar do", "kar dijiye", "likh do")):
        return str(staging.commit_pending(staging.current_thread()).get(
            "message_hi", "Ho gaya."))
    if words & _NO_WORDS or any(p in text for p in ("rehne do", "rahne do", "mat likho")):
        staging.clear_pending(staging.current_thread())
        return "Theek hai, kuch nahi likha."
    return "Haan ya nahi boliye."


# ===================================================================== read tools
@tool(parse_docstring=True)
def query_database(sql: str) -> str:
    """Database par ek READ-ONLY SQL SELECT chalaayein aur rows wapas karein.

    SIRF SELECT / WITH (read) queries allowed hain — INSERT/UPDATE/DELETE block
    ho jaati hain. Use for counts, sums, lists, "kitna / kaun / kab" jaise sawaal.
    Galat SQL par error + schema hint milta hai — sudhaar kar dobara try karein.

    TWO attached databases — table names MUST use the prefix (inv. / txn.):
    inv.inventory(item_id, name, category, brand, unit, qty, mrp, purchase_price, expiry_date, reorder_level, hsn, supplier)
    inv.suppliers(supplier_id, name, phone, focus)
    inv.purchases(purchase_id, item_id, supplier, qty, cost, ts)   -- restocks; cost = batch total
    txn.customers(customer_id, name, phone, credit_limit)
    txn.sales(sale_id, item_id, item_name, qty, sale_price, ts, customer_id)   -- item_name = item sold; customer_id NULL = walk-in
    txn.ledger(entry_id, customer_id, type, amount, items, due_date, ts)
        -- udhaar: type='debit' (taken) / 'credit' (repaid); balance = SUM(debit) - SUM(credit).
    qty = current stock. Money INR (₹). JOIN across freely, e.g.
      SELECT i.name, SUM(s.qty) FROM txn.sales s JOIN inv.inventory i ON i.item_id=s.item_id GROUP BY i.item_id.
    Dates are ISO strings; use date('now') / datetime('now','localtime'). Always SELECT, never write.

    Args:
        sql: Ek hi SELECT/WITH statement (SQLite syntax).
    """
    res = db.run_select(sql)
    if not res.get("ok"):
        hint = (
            "Use schema prefixes! "
            "inv.inventory(item_id,name,category,brand,unit,qty,mrp,purchase_price,expiry_date,reorder_level,hsn,supplier); "
            "inv.suppliers(supplier_id,name,phone,focus); inv.purchases(purchase_id,item_id,supplier,qty,cost,ts); "
            "txn.customers(customer_id,name,phone,credit_limit); "
            "txn.sales(sale_id,item_id,item_name,qty,sale_price,ts,customer_id); "
            "txn.ledger(entry_id,customer_id,type,amount,items,due_date,ts). Only SELECT/WITH allowed."
        )
        return f"SQL error: {res.get('error')}. Schema reminder:\n{hint}"
    text = _rows_to_text(res.get("rows", []), res.get("columns", []))
    if res.get("truncated"):
        text += "\n(results truncated — add LIMIT or aggregate for full data)"
    return text


@tool(parse_docstring=True)
def get_dashboard() -> str:
    """Aaj ka poora dashboard summary (stock value, bikri, expiry, low stock, udhaar).

    Ek nazar me dukaan ka haal — jab grahak "aaj ka hisaab / dashboard / overview"
    maange. Koi argument nahi.
    """
    snap = ops.dashboard_snapshot()
    sv = snap["stock_value"]
    today = snap["today"]
    exp = snap["expiring"]
    low = snap["low_stock"]
    udh = snap["udhaar"]
    slow = snap["slow_movers"]

    lines: list[str] = []
    lines.append(
        f"Stock value: {_money(sv['at_cost'])} (cost) / {_money(sv['at_mrp'])} (MRP), "
        f"{sv['total_units']} units, {sv['item_count']} items."
    )
    lines.append(
        f"Aaj ki bikri: {_money(today['revenue'])} ({today['units']} units, "
        f"{today['num_sales']} sales)."
    )
    if today.get("top_items"):
        tops = ", ".join(f"{t['name']} ({t['qty']})" for t in today["top_items"][:3])
        lines.append(f"Top bikri: {tops}.")
    lines.append(
        f"Expire hone wale ({config.EXPIRY_WARN_DAYS} din): "
        + (", ".join(f"{e['name']} [{e['days_left']}d]" for e in exp[:5]) if exp else "koi nahi")
        + "."
    )
    lines.append(
        "Stock kam: "
        + (", ".join(f"{l['name']} ({l['qty']})" for l in low[:5]) if low else "koi nahi")
        + "."
    )
    lines.append(
        f"Udhaar baaki: {_money(udh['total'])} ({udh['count']} grahak)."
    )
    if slow:
        lines.append("Slow movers: " + ", ".join(s["name"] for s in slow[:5]) + ".")
    return "\n".join(lines)


@tool(parse_docstring=True)
def get_item_detail(item_name: str) -> str:
    """Ek item ki poori detail — stock, MRP, margin, expiry, recent bikri.

    Use for "X ka kya haal / X ki detail / X kitna bacha".

    Args:
        item_name: Item ka naam (poora ya hissa, Hinglish chalega).
    """
    d = ops.item_detail(item_name)
    if not d.get("ok"):
        return f"'{item_name}' inventory me nahi mila."
    parts = [
        f"{d['name']}",
        f"category: {d.get('category') or '-'}",
        f"stock: {d['qty']}",
        f"MRP: {_money(d['mrp'])}",
        f"purchase: {_money(d['purchase_price'])}",
        f"margin: {_money(d['margin'])}",
        f"stock value (cost): {_money(d['stock_value_at_cost'])}",
        f"30-din bikri: {d.get('sold_last_30d', 0)} units",
    ]
    if d.get("expiry_date"):
        parts.append(f"expiry: {d['expiry_date']}")
    if d.get("last_sold"):
        parts.append(f"last sold: {d['last_sold']}")
    return " | ".join(parts) + "."


@tool(parse_docstring=True)
def get_customer_dues(customer_name: str = "") -> str:
    """Udhaar (credit) baaki dekhein — ek grahak ka ya sabhi ka kul.

    Naam diya ho to us grahak ka balance; khaali ho to poori dukaan ka pending
    udhaar (total + top grahak).

    Args:
        customer_name: Grahak ka naam (optional). Khaali = sabka summary.
    """
    name = _clean(customer_name)
    if name:
        bal = ops.customer_balance(name)
        if bal is None:
            return f"Customer '{customer_name}' khaate me nahi mila."
        phone = f" ({bal['phone']})" if bal.get("phone") else ""
        return f"{bal['customer']}{phone} ka baaki udhaar: {_money(bal['balance'])}."
    pend = ops.pending_udhaar()
    if not pend["customers"]:
        return "Koi udhaar baaki nahi hai."
    lines = [f"Kul pending udhaar: {_money(pend['total'])} ({pend['count']} grahak)."]
    for c in pend["customers"][:8]:
        flag = " [OVERDUE]" if c.get("overdue") else ""
        due = f", due {c['earliest_due']}" if c.get("earliest_due") else ""
        lines.append(f"- {c['name']}: {_money(c['balance'])}{due}{flag}")
    return "\n".join(lines)


# ============================================================================ list
TOOLS = [
    add_inventory_tool,
    record_sale_tool,
    record_purchase_tool,
    add_udhaar_tool,
    record_payment_tool,
    confirm_pending_tool,
    query_database,
    get_dashboard,
    get_item_detail,
    get_customer_dues,
]
