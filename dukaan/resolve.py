"""Inventory item resolution — merge incoming stock into the RIGHT existing row.

Incoming stock (challan lines, onboarding, restocks) arrives as free-text names
that vary in case, spacing, pack-size phrasing and brand order ("TATA SALT 1KG"
vs "Tata Salt 1kg"). The old ``ops.find_item`` used SQL ``LIKE`` and missed those
variants, so every variant spawned a DUPLICATE inventory row. This module scores
an incoming name against every existing row with a brand/HSN/size-aware fuzzy
score and decides one of three outcomes:

* ``matched``    — a clear winner (>= ACCEPT and well ahead of #2): merge qty here.
* ``ambiguous``  — plausible but not decisive (>= FLOOR): ask "kaun sa?" with
  the top candidates.
* ``none``       — nothing close enough: caller should create a new item.

The scorer normalises names (`normalize_item_name`) into ``base`` / ``brand`` /
``size`` parts so that two names sharing ONLY a size/unit token (e.g. a brand-new
"Qwxyz9 Testbrand 500ml 1kg pack") do NOT collide with an unrelated existing row.

Fuzzy matching uses ``rapidfuzz`` when available and falls back to the stdlib
``difflib.SequenceMatcher`` otherwise, so the module never hard-depends on it.
Reads reuse :func:`dukaan.db.qx`; thresholds default to
:data:`config.RESOLVE_ACCEPT` / :data:`config.RESOLVE_FLOOR`.
"""

from __future__ import annotations

import functools
import re

from dukaan import config, db

# --------------------------------------------------------------- fuzzy backend
# Prefer rapidfuzz.token_set_ratio; fall back to difflib so import never fails.
try:
    from rapidfuzz import fuzz as _rf_fuzz

    def _token_set_ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.token_set_ratio(a, b))

except Exception:  # pragma: no cover - exercised only when rapidfuzz is absent
    from difflib import SequenceMatcher

    def _token_set_ratio(a: str, b: str) -> float:
        # Order-insensitive approximation of token_set_ratio: compare the sorted
        # unique token sets (this is the cheap "set" component of the metric).
        sa = " ".join(sorted(set(a.split())))
        sb = " ".join(sorted(set(b.split())))
        if not sa and not sb:
            return 100.0
        return SequenceMatcher(None, sa, sb).ratio() * 100.0


# ----------------------------------------------------------------- normalising
# Pack-size + unit tokens, e.g. "1kg", "500 ml", "6pcs", "1.5l". Captured so the
# size string can be reused for tie-breaks before being stripped from the base.
_UNIT = (
    r"kg|g|gm|gram|ml|l|ltr|litre|liter|pc|pcs|pack|packet|piece|"
    r"box|loaf|cup|tin|jar|pouch|tetra|tray|sachet"
)
_SIZE_RE = re.compile(rf"\b(\d+(?:\.\d+)?)\s*({_UNIT})\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"(?:₹|rs\.?)\s*\d+(?:\.\d+)?", re.IGNORECASE)
_PAREN_RE = re.compile(r"\([^)]*\)")
_NONALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")


@functools.lru_cache(maxsize=1)
def _brand_vocab() -> frozenset[str]:
    """Lowercased brand vocabulary: DISTINCT inv.inventory brands ∪ seed brands.

    Cached for the process; "local"/"nan"/empties are dropped so a generic
    placeholder brand never gets treated as a meaningful brand token.
    """
    vocab: set[str] = set()
    try:
        for r in db.qx("SELECT DISTINCT lower(brand) AS b FROM inv.inventory"):
            b = (r.get("b") or "").strip()
            if b:
                vocab.add(b)
    except Exception:
        pass  # missing/empty DB -> rely on seed brands only
    try:
        from dukaan import seed_inventory as si

        for it in si.CATALOG:
            b = (it.get("brand") or "").strip().lower()
            if b:
                vocab.add(b)
    except Exception:
        pass
    vocab.discard("local")  # generic "unbranded" placeholder, not a real brand
    return frozenset(vocab)


def normalize_item_name(name: str) -> dict:
    """Split a free-text item name into matchable parts.

    Returns ``{"norm","base","brand","size","unit"}`` where ``norm`` is the fully
    cleaned string (lowercase, no price/paren/size/punctuation noise), ``brand``
    is the leading brand token(s) if recognised in the brand vocab, ``size`` is
    the first pack-size string seen (e.g. ``"1kg"``) with its ``unit``, and
    ``base`` is the remaining descriptive tokens joined.
    """
    raw = (name or "").lower()
    raw = _PAREN_RE.sub(" ", raw)      # drop "(...)" groups, e.g. "(rock salt)"
    raw = _PRICE_RE.sub(" ", raw)      # drop price tokens, e.g. "₹50" / "rs 20"

    # Remember the first pack-size before stripping ALL size tokens out.
    size, unit = "", ""
    m = _SIZE_RE.search(raw)
    if m:
        size = _WS_RE.sub("", m.group(0))           # "500 ml" -> "500ml"
        unit = m.group(2).lower()
    raw = _SIZE_RE.sub(" ", raw)       # strip every size+unit token from the name

    raw = _NONALNUM_RE.sub(" ", raw)   # drop remaining punctuation/symbols
    tokens = _WS_RE.sub(" ", raw).strip().split()
    norm = " ".join(tokens)

    # Brand = the longest recognised leading phrase (handles multi-word brands
    # like "tata sampann" / "india gate"), else the single leading token if it
    # alone is a known brand. Whatever is consumed is removed from the base.
    vocab = _brand_vocab()
    brand, consumed = "", 0
    for span in range(min(3, len(tokens)), 0, -1):
        phrase = " ".join(tokens[:span])
        if phrase in vocab:
            brand, consumed = phrase, span
            break
    base = " ".join(tokens[consumed:]) if brand else norm

    return {"norm": norm, "base": base, "brand": brand, "size": size, "unit": unit}


# --------------------------------------------------------------------- scoring
def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else (100.0 if x > 100.0 else x)


def _score(query: dict, row: dict) -> float:
    """Score 0..100 of how well a normalised ``query`` matches an inventory ``row``.

    Exact normalised-name equality is 100. Otherwise the base tokens are fuzzily
    compared (``token_set_ratio``), then nudged by hard signals: +12 same HSN,
    +8 same brand, -6 when both carry a size and they disagree. Clamped to 0..100.
    """
    rq = normalize_item_name(row.get("name") or "")
    if query["norm"] and query["norm"] == rq["norm"]:
        return 100.0

    score = _token_set_ratio(query["base"], rq["base"])

    q_hsn = (query.get("hsn") or "").strip()
    r_hsn = (row.get("hsn") or "").strip()
    if q_hsn and r_hsn and q_hsn == r_hsn:
        score += 12.0

    q_brand = query.get("brand") or ""
    if q_brand and q_brand == rq["brand"]:
        score += 8.0

    if query["size"] and rq["size"] and query["size"] != rq["size"]:
        score -= 6.0

    return _clamp(score)


# --------------------------------------------------------------------- resolve
def _scored_rows(query: dict, rows: list[dict]) -> list[dict]:
    """Attach a ``_score`` to each row and return them sorted best-first."""
    scored = []
    for row in rows:
        r = dict(row)
        r["_score"] = _score(query, row)
        scored.append(r)
    scored.sort(key=lambda r: r["_score"], reverse=True)
    return scored


def resolve_item(
    name: str,
    brand: str | None = None,
    hsn: str | None = None,
    *,
    accept: float = None,
    floor: float = None,
    top_k: int = 5,
) -> dict:
    """Resolve a free-text item name to an existing inventory row (or none).

    Returns ``{"status","item","score","candidates"}`` where ``status`` is:

    * ``"matched"``   — best >= ``accept`` AND best is >= 4 points ahead of #2
      (a clear winner); ``item`` is that row.
    * ``"ambiguous"`` — best >= ``floor`` but not a clear winner; ``candidates``
      holds the top ``top_k`` rows to disambiguate against.
    * ``"none"``      — nothing reached ``floor`` (caller should create a new item).

    ``brand`` / ``hsn`` override what was parsed from ``name`` when provided.
    Thresholds default to :data:`config.RESOLVE_ACCEPT` / :data:`config.RESOLVE_FLOOR`.
    """
    accept = config.RESOLVE_ACCEPT if accept is None else accept
    floor = config.RESOLVE_FLOOR if floor is None else floor

    query = normalize_item_name(name)
    if brand and brand.strip():
        query["brand"] = brand.strip().lower()
    query["hsn"] = (hsn or "").strip()

    rows = db.qx(
        "SELECT item_id, name, category, brand, unit, qty, mrp, purchase_price, "
        "expiry_date, reorder_level, hsn, supplier FROM inv.inventory"
    )
    if not rows:
        return {"status": "none", "item": None, "score": 0.0, "candidates": []}

    scored = _scored_rows(query, rows)

    # An exact full-name hit IS the item — a definitive identity signal that must
    # win even when unrelated rows tie at 100 on fuzzy base overlap (a one-token
    # base like "atta" is a token_set_ratio subset of other "...atta..." rows).
    # Identity is keyed on (norm, size): the size token is what distinguishes
    # same-name SKUs ("Saffola Gold Oil 1L" vs "5L"), so the full name resolves
    # uniquely while a size-less query stays genuinely ambiguous. Short-circuit
    # only when that exact hit is unique; true duplicate rows fall through.
    if query["norm"]:
        q_id = (query["norm"], query["size"])
        exact = [r for r in scored
                 if (lambda rq: (rq["norm"], rq["size"]) == q_id)(
                     normalize_item_name(r.get("name") or ""))]
        if len(exact) == 1:
            return {"status": "matched", "item": exact[0], "score": 100.0,
                    "candidates": scored[:top_k]}

    best = scored[0]
    best_score = float(best["_score"])
    second_score = float(scored[1]["_score"]) if len(scored) > 1 else 0.0

    if best_score >= accept and (best_score - second_score) >= 4.0:
        return {"status": "matched", "item": best, "score": best_score,
                "candidates": scored[:top_k]}
    if best_score >= floor:
        return {"status": "ambiguous", "item": None, "score": best_score,
                "candidates": scored[:top_k]}
    return {"status": "none", "item": None, "score": best_score, "candidates": []}


def candidates(name: str, limit: int = 5) -> list[dict]:
    """Top plausible inventory rows for ``name`` (score desc) for a disambiguation
    prompt. Each row carries a ``_score``; returns ``[]`` on an empty inventory."""
    query = normalize_item_name(name)
    query["hsn"] = ""
    rows = db.qx(
        "SELECT item_id, name, category, brand, unit, qty, mrp, purchase_price, "
        "expiry_date, reorder_level, hsn, supplier FROM inv.inventory"
    )
    if not rows:
        return []
    return _scored_rows(query, rows)[:limit]
