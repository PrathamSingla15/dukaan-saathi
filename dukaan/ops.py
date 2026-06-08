"""Business operations + analytics for Dukaan Saathi (two-database edition).

Pure Python over :mod:`dukaan.db`. Reads use the attached cross-DB read surface
(`db.qx`, schema-prefixed ``inv.*`` / ``txn.*``); writes route to the owning
database (`db.execute_inv` / `db.execute_txn`). Public signatures are unchanged
from the single-DB version, so tools / app / proactive need no edits.
"""

from __future__ import annotations

import datetime as dt
import re

from dukaan import config, db, resolve, shelf_life


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _money(x: float) -> str:
    return f"{config.CURRENCY}{x:,.0f}" if float(x).is_integer() else f"{config.CURRENCY}{x:,.2f}"


# ============================================================ fuzzy lookup helpers
def find_item(name: str) -> dict | None:
    """Resolve a (possibly Hinglish / partial) item name to an inventory row."""
    if not name:
        return None
    name = name.strip()
    rows = db.qx("SELECT * FROM inv.inventory WHERE lower(name)=lower(?)", (name,))
    if rows:
        return rows[0]
    rows = db.qx(
        "SELECT * FROM inv.inventory WHERE name LIKE ? COLLATE NOCASE ORDER BY length(name) LIMIT 1",
        (f"%{name}%",),
    )
    if rows:
        return rows[0]
    # Token fallback — SKIP size/unit tokens ("500ml", "1kg", "100g", "pack").
    units = {"ml", "g", "gm", "kg", "l", "ltr", "pc", "pcs", "pack", "packet",
             "piece", "box", "gram", "kilo", "litre", "liter"}
    for tok in re.split(r"\s+", name):
        t = tok.lower().strip("-.")
        if len(t) < 4 or any(c.isdigit() for c in t) or t in units:
            continue
        rows = db.qx(
            "SELECT * FROM inv.inventory WHERE name LIKE ? COLLATE NOCASE ORDER BY length(name) LIMIT 1",
            (f"%{t}%",),
        )
        if rows:
            return rows[0]
    return None


def find_customer(name: str) -> dict | None:
    if not name:
        return None
    name = name.strip()
    rows = db.qx("SELECT * FROM txn.customers WHERE lower(name)=lower(?)", (name,))
    if rows:
        return rows[0]
    rows = db.qx(
        "SELECT * FROM txn.customers WHERE name LIKE ? COLLATE NOCASE ORDER BY length(name) LIMIT 1",
        (f"%{name}%",),
    )
    return rows[0] if rows else None


def find_or_create_customer(name: str, phone: str | None = None) -> int:
    existing = find_customer(name)
    if existing:
        if phone and not existing.get("phone"):
            db.execute_txn("UPDATE customers SET phone=? WHERE customer_id=?",
                           (phone, existing["customer_id"]))
        return existing["customer_id"]
    return db.execute_txn("INSERT INTO customers(name, phone) VALUES (?,?)", (name.strip(), phone))


def customer_balance_by_id(cid: int) -> float:
    r = db.qx(
        "SELECT COALESCE(SUM(CASE WHEN type='debit' THEN amount ELSE -amount END),0) bal "
        "FROM txn.ledger WHERE customer_id=?",
        (cid,),
    )
    return round(r[0]["bal"], 2)


def customer_balance(name: str) -> dict | None:
    c = find_customer(name)
    if not c:
        return None
    return {"customer": c["name"], "phone": c.get("phone"),
            "balance": customer_balance_by_id(c["customer_id"])}


# ================================================ item/customer resolution (R3)
def resolve_item(name: str) -> dict:
    """Resolve an item name for the write/tool layer. Returns one of:
    {"status":"matched","item":row,"candidates":[]} |
    {"status":"ambiguous","item":None,"candidates":[rows]} |
    {"status":"none","item":None,"candidates":[]}."""
    r = resolve.resolve_item(name)
    return {"status": r["status"], "item": r.get("item"), "candidates": r.get("candidates", [])}


def candidates_item(name: str, limit: int = 5) -> list[dict]:
    """Top plausible inventory rows for a fuzzy/partial name (for 'kaun sa?')."""
    return resolve.candidates(name, limit)


def candidates_customer(name: str, limit: int = 5) -> list[dict]:
    if not name:
        return []
    return db.qx(
        "SELECT * FROM txn.customers WHERE name LIKE ? COLLATE NOCASE "
        "ORDER BY length(name) LIMIT ?",
        (f"%{name.strip()}%", limit),
    )


def resolve_customer(name: str) -> dict:
    """Resolve a customer name; mirror of resolve_item for the write/tool layer."""
    if not name or not name.strip():
        return {"status": "none", "customer": None, "candidates": []}
    exact = db.qx("SELECT * FROM txn.customers WHERE lower(name)=lower(?)", (name.strip(),))
    if exact:
        return {"status": "matched", "customer": exact[0], "candidates": []}
    cands = candidates_customer(name)
    if len(cands) == 1:
        return {"status": "matched", "customer": cands[0], "candidates": []}
    if len(cands) > 1:
        return {"status": "ambiguous", "customer": None, "candidates": cands}
    return {"status": "none", "customer": None, "candidates": []}


# ===================================================== lots / FEFO (expiry batches)
def estimate_expiry(name: str | None, category: str | None,
                    received: dt.date | None = None) -> tuple[str | None, bool]:
    """(expiry_iso_or_None, is_estimated) from category/SKU shelf-life when a
    challan/owner gives no date. Non-perishables -> (None, False)."""
    return shelf_life.estimate_expiry(name, category, received)


def _add_lot(item_id: int, qty: int, expiry_date: str | None, is_estimated: bool,
             supplier: str | None, purchase_price: float,
             received_ts: str | None = None) -> int:
    """Create-or-merge a stock lot. Merge into an existing OPEN lot ONLY when
    (item_id, expiry_date, is_estimated) all match — never clobber a prior batch's
    expiry. Returns the lot_id."""
    qty = int(qty)
    received_ts = received_ts or _now()
    rows = db.qx(
        "SELECT lot_id FROM inv.inventory_lots WHERE item_id=? AND is_estimated=? "
        "AND qty_remaining > 0 AND ((expiry_date IS NULL AND ? IS NULL) OR expiry_date=?) LIMIT 1",
        (item_id, int(is_estimated), expiry_date, expiry_date),
    )
    if rows:
        lot_id = rows[0]["lot_id"]
        sets = ["qty_received = qty_received + ?", "qty_remaining = qty_remaining + ?"]
        params: list = [qty, qty]
        if purchase_price:
            sets.append("purchase_price=?"); params.append(float(purchase_price))
        if supplier:
            sets.append("supplier=COALESCE(?, supplier)"); params.append(supplier)
        params.append(lot_id)
        db.execute_inv(f"UPDATE inventory_lots SET {', '.join(sets)} WHERE lot_id=?", params)
        return lot_id
    return db.execute_inv(
        "INSERT INTO inventory_lots(item_id, qty_received, qty_remaining, expiry_date, "
        "is_estimated, received_ts, supplier, purchase_price) VALUES (?,?,?,?,?,?,?,?)",
        (item_id, qty, qty, expiry_date, int(is_estimated), received_ts, supplier,
         float(purchase_price or 0)),
    )


def _consume_fefo(item_id: int, qty: int) -> dict:
    """Drain `qty` units from an item's lots earliest-expiry-first (NULL expiry
    last). Returns {ok, taken, shortfall, sold_from:[{lot_id,expiry_date,is_estimated,took}]}."""
    qty = int(qty)
    lots = db.qx(
        "SELECT lot_id, qty_remaining, expiry_date, is_estimated FROM inv.inventory_lots "
        "WHERE item_id=? AND qty_remaining > 0 ORDER BY (expiry_date IS NULL), expiry_date, lot_id",
        (item_id,),
    )
    remaining = qty
    sold_from: list[dict] = []
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, int(lot["qty_remaining"]))
        db.execute_inv("UPDATE inventory_lots SET qty_remaining = qty_remaining - ? WHERE lot_id=?",
                       (take, lot["lot_id"]))
        sold_from.append({"lot_id": lot["lot_id"], "expiry_date": lot["expiry_date"],
                          "is_estimated": bool(lot["is_estimated"]), "took": take})
        remaining -= take
    return {"ok": remaining <= 0, "taken": qty - remaining,
            "shortfall": max(0, remaining), "sold_from": sold_from}


def _recompute_item_qty(item_id: int) -> tuple[int, str | None]:
    """SINGLE writer of the cached aggregate: inventory.qty = SUM(open lots'
    qty_remaining); inventory.expiry_date = earliest open-lot expiry. Returns (qty, expiry)."""
    agg = db.qx(
        "SELECT COALESCE(SUM(qty_remaining),0) q, "
        "MIN(CASE WHEN qty_remaining > 0 THEN expiry_date END) e "
        "FROM inv.inventory_lots WHERE item_id=?",
        (item_id,),
    )[0]
    qty = int(agg["q"])
    expiry = agg["e"]
    db.execute_inv("UPDATE inventory SET qty=?, expiry_date=? WHERE item_id=?", (qty, expiry, item_id))
    return qty, expiry


def lots_for_item(item_id: int, open_only: bool = True) -> list[dict]:
    """An item's lots in FEFO order (for the item-detail panel / tests)."""
    where = "item_id=?" + (" AND qty_remaining > 0" if open_only else "")
    return db.qx(
        "SELECT lot_id, qty_received, qty_remaining, expiry_date, is_estimated, "
        "received_ts, supplier, purchase_price FROM inv.inventory_lots "
        f"WHERE {where} ORDER BY (expiry_date IS NULL), expiry_date, lot_id",
        (item_id,),
    )


def expiring_lots(days: int | None = None) -> list[dict]:
    """Earliest-expiry OPEN lot per item whose expiry is within `days`. Rows:
    item_id, name, category, qty (item total), expiry_date, days_left, is_estimated."""
    days = config.EXPIRY_WARN_DAYS if days is None else days
    return db.qx(
        "SELECT l.item_id, i.name, i.category, i.qty, l.expiry_date, "
        "  CAST(julianday(l.expiry_date) - julianday('now') AS INT) AS days_left, "
        "  l.is_estimated "
        "FROM inv.inventory_lots l JOIN inv.inventory i ON i.item_id = l.item_id "
        "WHERE l.qty_remaining > 0 AND l.expiry_date IS NOT NULL "
        "  AND l.lot_id = (SELECT l2.lot_id FROM inv.inventory_lots l2 "
        "                  WHERE l2.item_id = l.item_id AND l2.qty_remaining > 0 "
        "                    AND l2.expiry_date IS NOT NULL "
        "                  ORDER BY l2.expiry_date, l2.lot_id LIMIT 1) "
        "  AND julianday(l.expiry_date) - julianday('now') <= ? "
        "ORDER BY l.expiry_date",
        (days,),
    )


# ====================================================================== write ops
def add_inventory(name: str, qty: int, mrp: float | None = None,
                  purchase_price: float | None = None, category: str | None = None,
                  expiry_date: str | None = None, *, supplier: str | None = None,
                  resolved_item_id: int | None = None) -> dict:
    """Add stock for an item (restock if it resolves to an existing item, else
    create it). Stock is recorded as a FEFO lot and inventory.qty is recomputed
    from lots; when no expiry is given it is estimated from shelf-life (flagged
    is_estimated). Backwards-compatible return keys: ok/action/item/qty_added/
    new_qty/message (+ item_id/lot_id/expiry_date/is_estimated)."""
    qty = int(qty)
    if resolved_item_id is not None:
        rows = db.qx("SELECT * FROM inv.inventory WHERE item_id=?", (resolved_item_id,))
        item = rows[0] if rows else None
    else:
        r = resolve_item(name)
        # matched -> merge (variant dedup). ambiguous/none -> exact-first find_item
        # fallback (so an exact name still merges; only a truly new name creates).
        item = r["item"] if r["status"] == "matched" else find_item(name)

    if expiry_date:
        exp, is_est = expiry_date, False
    else:
        exp, is_est = estimate_expiry(name, category or (item or {}).get("category"))

    if item:
        item_id = item["item_id"]
        sets, params = [], []
        if mrp is not None:
            sets.append("mrp=?"); params.append(float(mrp))
        if purchase_price is not None:
            sets.append("purchase_price=?"); params.append(float(purchase_price))
        if category:
            sets.append("category=?"); params.append(category)
        if sets:
            params.append(item_id)
            db.execute_inv(f"UPDATE inventory SET {', '.join(sets)} WHERE item_id=?", params)
        lot_id = _add_lot(item_id, qty, exp, is_est, supplier,
                          float(purchase_price if purchase_price is not None
                                else item.get("purchase_price") or 0))
        new_qty, _ = _recompute_item_qty(item_id)
        nm = db.qx("SELECT name FROM inv.inventory WHERE item_id=?", (item_id,))[0]["name"]
        return {"ok": True, "action": "restock", "item": nm, "item_id": item_id,
                "qty_added": qty, "new_qty": new_qty, "lot_id": lot_id,
                "expiry_date": exp, "is_estimated": is_est,
                "message": f"{nm}: {qty} stock joda, ab kul {new_qty} hai."}

    item_id = db.execute_inv(
        "INSERT INTO inventory(name, category, qty, mrp, purchase_price, expiry_date, supplier) "
        "VALUES (?,?,?,?,?,?,?)",
        (name.strip(), category, 0, float(mrp or 0), float(purchase_price or 0), None, supplier),
    )
    lot_id = _add_lot(item_id, qty, exp, is_est, supplier, float(purchase_price or 0))
    new_qty, _ = _recompute_item_qty(item_id)
    return {"ok": True, "action": "new_item", "item": name.strip(), "item_id": item_id,
            "qty_added": qty, "new_qty": new_qty, "lot_id": lot_id,
            "expiry_date": exp, "is_estimated": is_est,
            "message": f"Naya item '{name.strip()}' add hua, stock {new_qty}."}


def record_sale(item_name: str, qty: int, sale_price: float | None = None,
                customer_name: str | None = None) -> dict:
    """Record a sale: oversell is HARD-BLOCKED (R2); stock drains FEFO (earliest
    expiry first) and inventory.qty is recomputed from lots."""
    qty = int(qty)
    item = find_item(item_name)
    if not item:
        return {"ok": False, "error": "item_not_found",
                "message": f"'{item_name}' inventory me nahi mila. Pehle item add karein."}
    available = int(item["qty"])
    if available < qty:
        # R2: refuse rather than let stock go negative.
        return {"ok": False, "error": "insufficient_stock", "item": item["name"],
                "requested": qty, "available": available, "remaining_stock": available,
                "message": (f"{item['name']} ka sirf {available} stock bacha hai — "
                            f"{qty} nahi bech sakte.")}
    price = float(sale_price) if sale_price is not None else float(item["mrp"])
    cid = find_or_create_customer(customer_name) if customer_name else None
    db.execute_txn(
        "INSERT INTO sales(item_id, item_name, qty, sale_price, ts, customer_id) VALUES (?,?,?,?,?,?)",
        (item["item_id"], item["name"], qty, price, _now(), cid),
    )
    drained = _consume_fefo(item["item_id"], qty)
    new_qty, _ = _recompute_item_qty(item["item_id"])
    warning = None
    if new_qty <= (item.get("reorder_level") or config.LOW_STOCK_THRESHOLD):
        warning = f"{item['name']} ka stock kam hai — sirf {new_qty} bacha."
    msg = f"{qty} × {item['name']} bika ({_money(qty * price)})."
    if customer_name:
        msg += f" Customer: {customer_name}."
    return {"ok": True, "item": item["name"], "qty": qty, "unit_price": price,
            "revenue": round(qty * price, 2), "remaining_stock": new_qty,
            "sold_from": drained["sold_from"], "warning": warning,
            "message": msg + (f" {warning}" if warning else "")}


def record_purchase(item_name: str, qty: int, cost: float | None = None, supplier: str | None = None,
                    purchase_price: float | None = None, mrp: float | None = None,
                    category: str | None = None, expiry_date: str | None = None, *,
                    resolved_item_id: int | None = None) -> dict:
    """Record a supplier purchase/restock: add a FEFO lot, log the purchase, and
    recompute inventory.qty (create the item if new). Expiry is estimated from
    shelf-life when the challan omits it (flagged is_estimated). Backwards-compatible
    return keys: ok/item/qty_added/new_qty/cost/supplier/message (+ lot_id/expiry_date/
    is_estimated)."""
    qty = int(qty)
    if resolved_item_id is not None:
        rows = db.qx("SELECT * FROM inv.inventory WHERE item_id=?", (resolved_item_id,))
        item = rows[0] if rows else None
    else:
        r = resolve_item(item_name)
        item = r["item"] if r["status"] == "matched" else find_item(item_name)

    unit_pp = (float(purchase_price) if purchase_price is not None
               else (float(cost) / qty if cost and qty
                     else (float(item["purchase_price"]) if item else 0.0)))
    if expiry_date:
        exp, is_est = expiry_date, False
    else:
        exp, is_est = estimate_expiry(item_name, category or (item or {}).get("category"))

    if item:
        item_id = item["item_id"]
        sets, params = [], []
        if purchase_price is not None:
            sets.append("purchase_price=?"); params.append(float(purchase_price))
        if mrp is not None:
            sets.append("mrp=?"); params.append(float(mrp))
        if category:
            sets.append("category=?"); params.append(category)
        if supplier:
            sets.append("supplier=?"); params.append(supplier)
        if sets:
            params.append(item_id)
            db.execute_inv(f"UPDATE inventory SET {', '.join(sets)} WHERE item_id=?", params)
    else:
        item_id = db.execute_inv(
            "INSERT INTO inventory(name, category, qty, mrp, purchase_price, expiry_date, supplier) "
            "VALUES (?,?,?,?,?,?,?)",
            (item_name.strip(), category, 0, float(mrp or 0), unit_pp, None, supplier),
        )
    lot_id = _add_lot(item_id, qty, exp, is_est, supplier, unit_pp)
    new_qty, _ = _recompute_item_qty(item_id)
    total_cost = float(cost) if cost is not None else round(qty * unit_pp, 2)
    db.execute_inv(
        "INSERT INTO purchases(item_id, supplier, qty, cost, ts) VALUES (?,?,?,?,?)",
        (item_id, supplier, qty, total_cost, _now()),
    )
    nm = db.qx("SELECT name FROM inv.inventory WHERE item_id=?", (item_id,))[0]["name"]
    sup = f" {supplier} se" if supplier else ""
    return {"ok": True, "item": nm, "qty_added": qty, "new_qty": new_qty, "lot_id": lot_id,
            "cost": total_cost, "supplier": supplier, "expiry_date": exp, "is_estimated": is_est,
            "message": f"{qty} × {nm}{sup} aaya ({_money(total_cost)}). Ab stock {new_qty}."}


def add_udhaar(customer_name: str, amount: float, items: str | None = None,
               due_date: str | None = None, phone: str | None = None) -> dict:
    """Add a credit (udhaar) entry for a customer (creates the customer if new)."""
    cid = find_or_create_customer(customer_name, phone)
    db.execute_txn(
        "INSERT INTO ledger(customer_id, type, amount, items, due_date, ts) VALUES (?,?,?,?,?,?)",
        (cid, "debit", float(amount), items, due_date, _now()),
    )
    name = db.qx("SELECT name FROM txn.customers WHERE customer_id=?", (cid,))[0]["name"]
    bal = customer_balance_by_id(cid)
    return {"ok": True, "customer": name, "added": float(amount), "balance": bal,
            "message": f"{name} ke khate me {_money(amount)} udhaar joda. Total baaki {_money(bal)}."}


def record_payment(customer_name: str, amount: float) -> dict:
    """Record a repayment (credit) against a customer's udhaar."""
    c = find_customer(customer_name)
    if not c:
        return {"ok": False, "error": "customer_not_found",
                "message": f"Customer '{customer_name}' khaate me nahi mila."}
    db.execute_txn(
        "INSERT INTO ledger(customer_id, type, amount, items, due_date, ts) VALUES (?,?,?,?,?,?)",
        (c["customer_id"], "credit", float(amount), None, None, _now()),
    )
    bal = customer_balance_by_id(c["customer_id"])
    return {"ok": True, "customer": c["name"], "paid": float(amount), "balance": bal,
            "message": f"{c['name']} ne {_money(amount)} jama kiya. Baaki {_money(bal)}."}


# ======================================================================= analytics
def stock_value() -> dict:
    r = db.qx(
        "SELECT COALESCE(SUM(qty*purchase_price),0) cost, COALESCE(SUM(qty*mrp),0) mrp, "
        "COALESCE(SUM(qty),0) units, COUNT(*) items FROM inv.inventory"
    )[0]
    return {"at_cost": round(r["cost"], 2), "at_mrp": round(r["mrp"], 2),
            "potential_margin": round(r["mrp"] - r["cost"], 2),
            "total_units": r["units"], "item_count": r["items"]}


def expiring_soon(days: int | None = None) -> list[dict]:
    """Near-expiry feed (lots-aware: earliest OPEN lot per item). Keeps the
    item_id/name/category/qty/expiry_date/days_left keys; adds is_estimated."""
    return expiring_lots(days)


def low_stock(threshold: int | None = None) -> list[dict]:
    """Items at/below their reorder level (falling back to a flat threshold)."""
    threshold = config.LOW_STOCK_THRESHOLD if threshold is None else threshold
    return db.qx(
        "SELECT item_id, name, category, qty, reorder_level FROM inv.inventory "
        "WHERE qty <= CASE WHEN reorder_level > 0 THEN reorder_level ELSE ? END ORDER BY qty",
        (threshold,),
    )


def udhaar_balances() -> list[dict]:
    rows = db.qx(
        "SELECT c.customer_id, c.name, c.phone, "
        "  ROUND(SUM(CASE WHEN l.type='debit' THEN l.amount ELSE -l.amount END),2) AS balance, "
        "  MIN(CASE WHEN l.type='debit' THEN l.due_date END) AS earliest_due "
        "FROM txn.ledger l JOIN txn.customers c ON c.customer_id=l.customer_id "
        "GROUP BY c.customer_id HAVING balance > 0.001 ORDER BY balance DESC"
    )
    today = dt.date.today().isoformat()
    for r in rows:
        r["overdue"] = bool(r["earliest_due"] and r["earliest_due"] < today)
    return rows


def pending_udhaar() -> dict:
    rows = udhaar_balances()
    return {"total": round(sum(r["balance"] for r in rows), 2),
            "count": len(rows), "customers": rows}


def overdue_udhaar() -> list[dict]:
    return [r for r in udhaar_balances() if r["overdue"]]


def today_summary() -> dict:
    agg = db.qx(
        "SELECT COALESCE(SUM(qty*sale_price),0) rev, COALESCE(SUM(qty),0) units, COUNT(*) n "
        "FROM txn.sales WHERE date(ts)=date('now','localtime')"
    )[0]
    top = db.qx(
        "SELECT item_name AS name, SUM(qty) qty, ROUND(SUM(qty*sale_price),2) revenue "
        "FROM txn.sales WHERE date(ts)=date('now','localtime') "
        "GROUP BY item_name ORDER BY qty DESC LIMIT 5"
    )
    return {"revenue": round(agg["rev"], 2), "units": agg["units"],
            "num_sales": agg["n"], "top_items": top}


def slow_movers(days: int | None = None) -> list[dict]:
    """Items that still have stock but recorded NO sales in the last `days`."""
    days = config.SLOW_MOVER_DAYS if days is None else days
    return db.qx(
        "SELECT i.item_id, i.name, i.category, i.qty, MAX(s.ts) AS last_sold, "
        "  COALESCE(SUM(CASE WHEN s.ts >= datetime('now', ?) THEN s.qty END),0) AS sold_recent "
        "FROM inv.inventory i LEFT JOIN txn.sales s ON s.item_id = i.item_id "
        "WHERE i.qty > 0 GROUP BY i.item_id HAVING sold_recent = 0 ORDER BY i.qty DESC",
        (f"-{days} days",),
    )


def sales_trend(item_name: str, days: int = 30) -> dict:
    item = find_item(item_name)
    if not item:
        return {"ok": False, "error": "item_not_found", "item": item_name}
    daily = db.qx(
        "SELECT date(ts) d, SUM(qty) qty, ROUND(SUM(qty*sale_price),2) revenue "
        "FROM txn.sales WHERE item_id=? AND ts >= datetime('now', ?) GROUP BY date(ts) ORDER BY d",
        (item["item_id"], f"-{days} days"),
    )
    tot = db.qx(
        "SELECT COALESCE(SUM(qty),0) qty, MAX(ts) last_sold FROM txn.sales WHERE item_id=?",
        (item["item_id"],),
    )[0]
    return {"ok": True, "item": item["name"], "category": item["category"],
            "brand": item.get("brand"), "qty_in_stock": item["qty"],
            "expiry_date": item["expiry_date"], "mrp": item["mrp"],
            "purchase_price": item["purchase_price"], "window_days": days, "daily": daily,
            "sold_in_window": sum(d["qty"] for d in daily),
            "total_qty_all_time": tot["qty"], "last_sold": tot["last_sold"]}


def item_detail(name: str) -> dict:
    item = find_item(name)
    if not item:
        return {"ok": False, "error": "item_not_found", "item": name}
    trend = sales_trend(item["name"], days=30)
    return {"ok": True, **item, "margin": round(item["mrp"] - item["purchase_price"], 2),
            "stock_value_at_cost": round(item["qty"] * item["purchase_price"], 2),
            "sold_last_30d": trend.get("sold_in_window", 0), "last_sold": trend.get("last_sold"),
            "lots": lots_for_item(item["item_id"])}


def dashboard_snapshot() -> dict:
    """Everything the Gradio 'today' dashboard needs in one call."""
    return {"stock_value": stock_value(), "today": today_summary(),
            "expiring": expiring_soon(), "low_stock": low_stock(),
            "udhaar": pending_udhaar(), "slow_movers": slow_movers()}
