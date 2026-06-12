"""Research-grounded seed data for ``transactions.db`` (sales & udhaar/khata).

This module is pure, deterministic and import-safe -- no network, no file IO and
no heavy dependencies (stdlib ``datetime`` / ``random`` only). It is consumed by
the loader in :mod:`dukaan.db`, which reads :data:`CUSTOMERS` and calls
:func:`generate` with the inventory ``CATALOG`` and an ``end_date``.

It models a real mid-2026 neighbourhood kirana's till and khata register over a
trailing ~120-day window, kept CONSISTENT with :mod:`dukaan.seed_inventory`:

* Every sold ``item_name`` is an exact ``CATALOG`` name; ``sale_price`` is that
  SKU's MRP, with an occasional small "chhoot" (discount) of a rupee or two.
* Fast-moving categories (Dairy, Bread, Eggs, Biscuits, Tea & Coffee, Water,
  Cold Drinks, Snacks, Staples) sell every single day in volume; a handful of
  deliberate slow movers (premium chocolate, big dry-fruit packs, infant
  formula, specialty oils) barely move.
* ~60-70% of bills are anonymous walk-ins (``customer_name=None``); the rest are
  named regulars. Saturdays/Sundays get a footfall bump and a single ~week-long
  festival window (a Diwali-style spike around 20 Oct -> 5 Nov, matching the
  inventory restock model) lifts both volume and basket size.
* A subset of the regulars run a khata: several ``debit`` purchases on credit
  with a short item note, partial ``credit`` repayments, and SEVERAL customers
  left with an outstanding balance whose earliest unpaid debit is already
  OVERDUE (``due_date`` in the past relative to ``end_date``).

Determinism: a private :class:`random.Random` seeded from the ``seed`` argument
drives everything -- the global ``random`` module is never touched and there is
no wall-clock / ``date.today`` dependence inside :func:`generate`.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Optional

# --------------------------------------------------------------------------- types
# A CUSTOMER dict: {"name", "phone", "credit_limit"}
# A SALE dict:     {"item_name", "qty", "sale_price", "ts", "customer_name"}
# A LEDGER dict:   {"customer_name", "type", "amount", "items", "due_date", "ts"}


# ====================================================================== customers
# The store's regulars: ~35 households/shopkeepers the kirana knows by name. Each
# is recorded the way an owner actually keeps a bahi-khata: a full name PLUS a
# vague Hinglish location/landmark tag in parentheses -- the little identifier the
# owner scribbles to tell similar customers apart ("Rakesh -- Gali 4", "Munna --
# auto wale"). The tag is owner-facing only; the customer-facing WhatsApp reminder
# strips it (see dukaan.proactive._greeting_name). ``credit_limit`` is the informal
# udhaar ceiling the owner extends -- bigger for trusted old families and nearby
# small businesses, modest for newer faces.
CUSTOMERS: list[dict] = [
    {"name": "Rakesh Sharma (Gali No. 4)", "phone": "98110 24517", "credit_limit": 5000.0},
    {"name": "Mahesh Verma (Shastri Nagar)", "phone": "98100 31882", "credit_limit": 4000.0},
    {"name": "Sunita Gupta (DDA Flats, B-Block)", "phone": "99581 47203", "credit_limit": 3500.0},
    {"name": "Rafiq Khan (Subzi Mandi ke paas)", "phone": "98184 60219", "credit_limit": 6000.0},
    {"name": "Lakshmi Menon (Railway Colony)", "phone": "98112 55703", "credit_limit": 2500.0},
    {"name": "Venkat Reddy (Teen Batti)", "phone": "98715 38044", "credit_limit": 5000.0},
    {"name": "Iqbal Ahmed (Idgah ke paas)", "phone": "99102 84718", "credit_limit": 4500.0},
    {"name": "Dinesh Mehta (Naya Bazaar)", "phone": "98990 71256", "credit_limit": 3000.0},
    {"name": "Suresh Pandey (Hanuman Mandir ke peeche)", "phone": "98109 64043", "credit_limit": 3500.0},
    {"name": "Tapan Das (Kumhar Toli)", "phone": "98711 26055", "credit_limit": 2000.0},
    {"name": "Gurmeet Singh (Model Town)", "phone": "99996 41720", "credit_limit": 5500.0},
    {"name": "Prakash Joshi (Petrol pump ke paas)", "phone": "98115 90340", "credit_limit": 3000.0},
    {"name": "Raman Iyer (Gandhi Chowk)", "phone": "98919 67012", "credit_limit": 4000.0},
    {"name": "Geeta Nair (Ashok Nagar)", "phone": "98101 33725", "credit_limit": 2500.0},
    {"name": "Sujoy Banerjee (Lake Road)", "phone": "98738 41960", "credit_limit": 3000.0},
    {"name": "Mahipal Chaudhary (Sadar Bazaar)", "phone": "98110 77342", "credit_limit": 6000.0},
    {"name": "Anil Saxena (Civil Lines)", "phone": "98100 22914", "credit_limit": 3500.0},
    {"name": "Narayan Rao (Ambedkar Colony)", "phone": "99581 60483", "credit_limit": 4500.0},
    {"name": "Fatima Begum (Masjid wali gali)", "phone": "98184 11927", "credit_limit": 2500.0},
    {"name": "Munna Yadav (auto wale)", "phone": "98112 70655", "credit_limit": 3000.0},
    {"name": "Shyam Mishra (Purana Mandir ke paas)", "phone": "98715 03844", "credit_limit": 4000.0},
    {"name": "Vijay Kapoor (Kothi No. 12)", "phone": "99102 48871", "credit_limit": 5000.0},
    {"name": "Girish Bhatt (Tanki wali gali)", "phone": "98990 17562", "credit_limit": 2500.0},
    {"name": "Radha Pillai (LIG Quarters)", "phone": "98109 46430", "credit_limit": 3000.0},
    {"name": "Nikhil Ghosh (Bara Bazaar)", "phone": "98711 62055", "credit_limit": 3500.0},
    {"name": "Aslam Sheikh (Nai Basti)", "phone": "99996 14207", "credit_limit": 4000.0},
    {"name": "Harish Trivedi (School ke saamne)", "phone": "98115 09034", "credit_limit": 3000.0},
    {"name": "Krishnan Menon (Bus stand ke paas)", "phone": "98919 76120", "credit_limit": 3500.0},
    {"name": "Devendra Shukla (Bank wali gali)", "phone": "98101 13572", "credit_limit": 4000.0},
    {"name": "Awadhesh Dubey (Chungi ke paas)", "phone": "98738 14906", "credit_limit": 2500.0},
    {"name": "Rashid Mansoori (Kabaadi market)", "phone": "98110 33429", "credit_limit": 4500.0},
    {"name": "Prasad Naidu (Gandhi Nagar)", "phone": "98100 73329", "credit_limit": 3000.0},
    {"name": "Rina Sengupta (Naya Para)", "phone": "99581 04927", "credit_limit": 2500.0},
    {"name": "Jignesh Patel (Station Road)", "phone": "98184 16203", "credit_limit": 5000.0},
    {"name": "Saleem Ansari (Bunkar Colony)", "phone": "98112 50719", "credit_limit": 3500.0},
]

_CUSTOMER_NAMES = [c["name"] for c in CUSTOMERS]


# =================================================================== sales model
# Fast-moving kirana categories that turn over every single day in volume.
_FAST_CATS: frozenset[str] = frozenset({
    "Dairy", "Bread", "Eggs", "Biscuits", "Tea & Coffee", "Water",
    "Cold Drinks", "Snacks", "Staples",
})

# Mid-movers: sell most days but in thinner volume than the fast movers.
_MID_CATS: frozenset[str] = frozenset({
    "Edible Oil", "Spices", "Confectionery", "Instant Food",
    "Personal Care", "Home Care", "Tobacco",
})

# Per-category typical "lines per day" pressure (how many distinct sale lines of
# that category the store rings up on an average weekday). Fast movers dominate
# the till tape; everything not listed falls back to a small default.
_DAILY_LINES: dict[str, float] = {
    "Dairy": 6.0,
    "Bread": 3.0,
    "Eggs": 3.0,
    "Biscuits": 4.0,
    "Snacks": 4.5,
    "Cold Drinks": 4.0,
    "Water": 2.5,
    "Tea & Coffee": 2.0,
    "Staples": 3.5,
    "Confectionery": 2.5,
    "Instant Food": 2.5,
    "Edible Oil": 1.2,
    "Spices": 1.2,
    "Personal Care": 1.5,
    "Home Care": 1.0,
    "Tobacco": 2.0,
    "Pooja": 0.5,
    "Stationery": 0.6,
    "Dry Fruits": 0.25,
    "Baby Care": 0.3,
}
_DEFAULT_DAILY_LINES = 0.4

# Deliberate slow movers: high-ticket / occasion items that barely sell. These
# get only rare lines regardless of their category's base pressure.
_SLOW_ITEMS: frozenset[str] = frozenset({
    "Cadbury Dairy Milk 200g",
    "Saffola Gold Oil 5L",
    "Fortune Mustard Oil 5L",
    "Patanjali Cow Ghee 1L",
    "Walnuts (Akhrot) loose",
    "Cashews (Kaju) loose",
    "Nestle Nan Pro Stage 1 400g",
    "Huggies Wonder Pants S (9)",
    "Kinley Water 20L Can",
    "Johnson's Baby Powder 200g",
})

# Festival window (Diwali-style), aligned with the inventory restock model so the
# two seeds tell one coherent story. Both volume and basket size lift here.
_FESTIVE_CATS: frozenset[str] = frozenset({
    "Staples", "Edible Oil", "Dry Fruits", "Confectionery", "Snacks",
    "Dairy", "Spices", "Pooja", "Tea & Coffee",
})


def _is_festive(d: dt.date) -> bool:
    """A single ~week-long festival spike: 28 Oct -> 4 Nov (Diwali-style)."""
    return (d.month == 10 and 28 <= d.day <= 31) or (d.month == 11 and 1 <= d.day <= 4)


def _typical_qty(item: dict, rnd: random.Random) -> int:
    """A believable units-per-line for this SKU. Cheap single-serve impulse buys
    (Rs1-Rs20 candy/chips/sachets) go in small handfuls; staples/oil one or two."""
    mrp = float(item["mrp"])
    cat = item["category"]
    if cat in ("Confectionery", "Snacks") and mrp <= 20:
        return rnd.randint(1, 6)
    if cat == "Eggs" and item["mrp"] <= 10:  # loose eggs sold by the half-dozen
        return rnd.choice((2, 4, 6, 6, 12))
    if cat in ("Dairy", "Bread"):
        return rnd.choice((1, 1, 1, 2, 2, 3))
    if cat in ("Edible Oil", "Staples") and mrp >= 250:
        return 1
    if mrp >= 200:
        return 1
    return rnd.choice((1, 1, 1, 2, 2, 3, 4))


def _sale_price(item: dict, rnd: random.Random) -> float:
    """Sale price = MRP, with an occasional small bhav-taav discount (a rupee or
    two, ~1 in 8 lines) -- never above MRP."""
    mrp = float(item["mrp"])
    if mrp >= 50 and rnd.random() < 0.12:
        cut = rnd.choice((1, 2, 2, 5)) if mrp >= 150 else rnd.choice((1, 2))
        return round(max(1.0, mrp - cut), 2)
    return round(mrp, 2)


def _sale_ts(d: dt.date, rnd: random.Random) -> str:
    """A plausible till timestamp. Footfall clusters morning (milk/bread/eggs run)
    and evening (after-work groceries); the shop runs ~07:00-22:00."""
    bucket = rnd.random()
    if bucket < 0.4:
        hour = rnd.randint(7, 11)       # morning rush
    elif bucket < 0.6:
        hour = rnd.randint(12, 16)      # midday lull
    else:
        hour = rnd.randint(17, 22)      # evening rush
    minute = rnd.randint(0, 59)
    second = rnd.randint(0, 59)
    return dt.datetime(d.year, d.month, d.day, hour, minute, second).isoformat(timespec="seconds")


def _build_sales(catalog: list[dict], start: dt.date, days: int, rnd: random.Random) -> list[dict]:
    """Ring up ~120 days of till lines. For each day we draw a Poisson-ish count
    of lines per category (scaled up on weekends/festivals), pick a SKU weighted
    toward cheaper fast movers, and attach a customer ~1 line in 3."""
    # Per-category SKU pools, with a per-SKU pick weight (cheaper + fast => sells
    # more often; flagged slow movers heavily down-weighted).
    pools: dict[str, list[tuple[dict, float]]] = {}
    for it in catalog:
        cat = it["category"]
        mrp = float(it["mrp"])
        if it["name"] in _SLOW_ITEMS:
            w = 0.05
        else:
            # cheaper items get picked more often; gentle taper by price band
            if mrp <= 20:
                w = 3.0
            elif mrp <= 50:
                w = 2.0
            elif mrp <= 120:
                w = 1.2
            elif mrp <= 300:
                w = 0.6
            else:
                w = 0.25
        pools.setdefault(cat, []).append((it, w))

    out: list[dict] = []
    for offset in range(days):
        d = start + dt.timedelta(days=offset)
        is_weekend = d.weekday() >= 5          # Sat=5, Sun=6
        festive = _is_festive(d)

        # Day-level demand multiplier: weekends busier, festival week much busier,
        # plus mild day-to-day noise so no two days look identical.
        mult = 1.0
        if is_weekend:
            mult *= rnd.uniform(1.25, 1.5)
        if festive:
            mult *= rnd.uniform(1.8, 2.4)
        mult *= rnd.uniform(0.85, 1.15)

        for cat, pool in pools.items():
            base = _DAILY_LINES.get(cat, _DEFAULT_DAILY_LINES)
            lam = base * mult
            if festive and cat in _FESTIVE_CATS:
                lam *= rnd.uniform(1.3, 1.8)   # extra festive pull on key categories
            # number of distinct lines of this category today (small-count draw)
            n_lines = _poisson_like(lam, rnd)
            if n_lines <= 0:
                continue

            items = [p[0] for p in pool]
            weights = [p[1] for p in pool]
            for _ in range(n_lines):
                item = rnd.choices(items, weights=weights, k=1)[0]
                qty = _typical_qty(item, rnd)
                if festive and cat in _FESTIVE_CATS:
                    qty += rnd.randint(0, 2)   # fuller festive baskets
                price = _sale_price(item, rnd)

                # ~65% walk-ins (no name); the rest attach to a known regular.
                if rnd.random() < 0.35:
                    customer = rnd.choice(_CUSTOMER_NAMES)
                else:
                    customer = None

                out.append({
                    "item_name": item["name"],
                    "qty": int(qty),
                    "sale_price": float(price),
                    "ts": _sale_ts(d, rnd),
                    "customer_name": customer,
                })

    out.sort(key=lambda r: r["ts"])
    return out


def _poisson_like(lam: float, rnd: random.Random) -> int:
    """Small-count draw approximating Poisson(lam) using Knuth's algorithm. Kept
    local (no ``math`` import) -- exp(-lam) via a cheap series for the small lams
    we use here. Returns a non-negative int line count."""
    if lam <= 0:
        return 0
    # exp(-lam) without math: 1/exp(lam), exp(lam) by truncated Taylor series.
    # lam stays modest (<~15) so a 60-term series is comfortably accurate.
    exp_lam = 1.0
    term = 1.0
    for k in range(1, 60):
        term *= lam / k
        exp_lam += term
        if term < 1e-12:
            break
    limit = 1.0 / exp_lam
    k = 0
    p = 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1


# ============================================================= udhaar / khata model
# A "khata note" is the short free-text the owner scribbles next to a credit
# entry. We synthesise believable ones from the items actually bought on credit.
_KHATA_NOTE_POOL: list[str] = [
    "atta, dal, tel",
    "doodh aur bread",
    "Maggi, biscuit, namkeen",
    "chai patti, sugar, doodh",
    "monthly ration",
    "rice, oil, masala",
    "doodh (poora hafta)",
    "soap, shampoo, detergent",
    "bachchon ka samaan",
    "cold drink, chips, chocolate",
    "ghee, paneer, dahi",
    "atta, chawal, cheeni",
    "tel aur masala",
    "festival ka samaan",
    "eggs, bread, butter",
]


def _khata_ts(d: dt.date, rnd: random.Random) -> str:
    """Timestamp for a khata entry -- same trading hours as the till."""
    hour = rnd.randint(8, 21)
    minute = rnd.randint(0, 59)
    return dt.datetime(d.year, d.month, d.day, hour, minute).isoformat(timespec="seconds")


def _build_ledger(start: dt.date, end_date: dt.date, days: int, rnd: random.Random) -> list[dict]:
    """Run a khata for ~12-18 regulars. Each accrues several debits across the
    window with partial repayments; we deliberately leave SEVERAL with an
    outstanding balance whose earliest unpaid debit is already OVERDUE."""
    n_khata = rnd.randint(13, 17)
    khata_customers = rnd.sample(_CUSTOMER_NAMES, n_khata)

    out: list[dict] = []
    # Guarantee a healthy number of clearly-overdue accounts: the first ~6 khata
    # customers are forced into "still owes, earliest unpaid debit in the past".
    forced_overdue = set(khata_customers[: min(6, n_khata)])

    for cust in khata_customers:
        n_debits = rnd.randint(3, 7)
        # Spread debit dates across the window (avoid the final fortnight so a
        # later credit/overdue due_date still lands in the period).
        latest_debit_offset = max(20, days - 14)
        debit_offsets = sorted(rnd.sample(range(2, latest_debit_offset), n_debits))

        events: list[dict] = []   # (offset, kind, amount, note, due_date) staged
        outstanding = 0.0
        debit_records: list[tuple[int, float]] = []  # (offset, amount) for credits

        for i, off in enumerate(debit_offsets):
            # Real khata amounts are a MIX: mostly round ("de do 500 ka"), some exact
            # to the rupee (an itemised basket). Pool leans round (common ₹300/₹500
            # appear twice) with a ~25% sprinkle of odd figures, so balances come out
            # a believable blend of clean (₹1,900, ₹2,000) and odd (₹1,832) — neither
            # uniformly round nor uniformly odd (both read as synthetic).
            amount = float(rnd.choice((100, 150, 200, 250, 300, 300, 350, 400, 450, 500,
                                       500, 550, 600, 650, 700, 750, 285, 465)))
            note = rnd.choice(_KHATA_NOTE_POOL)
            # Khata terms: settle within ~2-3 weeks. due_date = debit date + term.
            term = rnd.choice((10, 14, 14, 21, 30))
            due = start + dt.timedelta(days=off + term)
            events.append({
                "customer_name": cust,
                "type": "debit",
                "amount": round(amount, 2),
                "items": note,
                "due_date": due.isoformat(),
                "ts": _khata_ts(start + dt.timedelta(days=off), rnd),
            })
            outstanding += amount
            debit_records.append((off, amount))

            # A partial repayment usually lands a little after each debit (but the
            # last debit is often still unpaid -> leaves a running balance).
            is_last = i == n_debits - 1
            repay_chance = 0.55 if not is_last else (0.15 if cust in forced_overdue else 0.5)
            if rnd.random() < repay_chance and outstanding > 0:
                frac = rnd.uniform(0.3, 0.9)
                # Customers repay in round notes (₹500, ₹1000) -> round to nearest ₹50.
                pay = round(min(outstanding, max(50.0, amount * frac)) / 50.0) * 50.0
                pay = max(50.0, pay)
                pay_off = off + rnd.randint(3, 12)
                if pay_off < days:
                    events.append({
                        "customer_name": cust,
                        "type": "credit",
                        "amount": round(float(pay), 2),
                        "items": None,
                        "due_date": None,
                        "ts": _khata_ts(start + dt.timedelta(days=pay_off), rnd),
                    })
                    outstanding -= pay

        # For forced-overdue customers, make sure they still owe something AND the
        # earliest unpaid debit's due_date is in the past. The earliest debit's due
        # date is already < end_date by construction (offset small + term), and we
        # ensure outstanding > 0 by not over-crediting them.
        if cust in forced_overdue and outstanding <= 0:
            # Add a fresh unpaid debit early enough to be overdue.
            off = rnd.randint(2, max(3, days // 3))
            amount = float(rnd.choice((300, 418, 489, 550)))
            due = start + dt.timedelta(days=off + rnd.choice((10, 14)))
            events.append({
                "customer_name": cust,
                "type": "debit",
                "amount": round(amount, 2),
                "items": rnd.choice(_KHATA_NOTE_POOL),
                "due_date": due.isoformat(),
                "ts": _khata_ts(start + dt.timedelta(days=off), rnd),
            })

        out.extend(events)

    out.sort(key=lambda r: r["ts"])
    return out


# ==================================================================== public API
def generate(catalog: list[dict], end_date: dt.date, seed: int = 42) -> dict:
    """Build deterministic sales + udhaar history for the trailing ~120 days.

    Parameters
    ----------
    catalog:
        The inventory ``CATALOG`` (list of SKU dicts). Sale ``item_name`` values
        are drawn exclusively from ``it["name"]`` and ``sale_price`` from
        ``it["mrp"]`` so the two databases stay perfectly consistent.
    end_date:
        A :class:`datetime.date`; the window is the 120 days ending on this date.
    seed:
        Seed for the private :class:`random.Random` (default 42). The global
        ``random`` module is never used, so output is fully reproducible.

    Returns
    -------
    dict
        ``{"sales": [...], "ledger": [...]}`` -- see the module docstring for the
        per-row schema.
    """
    rnd = random.Random(seed)
    days = 120
    start = end_date - dt.timedelta(days=days)

    sales = _build_sales(catalog, start, days, rnd)
    ledger = _build_ledger(start, end_date, days, rnd)
    return {"sales": sales, "ledger": ledger}


# --------------------------------------------------------------- tiny self-summary
def _outstanding_by_customer(ledger: list[dict]) -> dict[str, float]:
    """Running udhaar balance per customer (debit - credit). Handy for tests."""
    bal: dict[str, float] = {}
    for row in ledger:
        sign = 1.0 if row["type"] == "debit" else -1.0
        bal[row["customer_name"]] = bal.get(row["customer_name"], 0.0) + sign * float(row["amount"])
    return {k: round(v, 2) for k, v in bal.items()}


if __name__ == "__main__":  # pragma: no cover - manual inspection only
    _end = dt.date.today()
    # tiny stub catalog only so this module runs standalone; db.py passes the real one
    _stub = [{"name": "Amul Gold Milk 500ml", "category": "Dairy", "mrp": 34}]
    data = generate(_stub, _end)
    print(f"CUSTOMERS: {len(CUSTOMERS)}")
    print(f"sales:  {len(data['sales'])} lines")
    print(f"ledger: {len(data['ledger'])} entries")
    bals = _outstanding_by_customer(data["ledger"])
    owing = {k: v for k, v in bals.items() if v > 0}
    print(f"khata customers still owing: {len(owing)}")
