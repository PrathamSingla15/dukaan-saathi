"""Shelf-life estimation helpers for Dukaan Saathi.

Derives expiry dates from per-SKU ``shelf_life_days`` encoded in
:mod:`dukaan.catalog`, using exact-match → fuzzy-match → category
median resolution.  Pure module: no database access, no network, no config
dependency.
"""

from __future__ import annotations

import datetime
import difflib
import statistics
from typing import Optional

from dukaan.catalog import CATALOG

# ---------------------------------------------------------------------------
# Module-level lookup maps built once at import time.

# name (lower-cased) → shelf_life_days (int or None).
# None values are intentionally kept so that an exact match on a non-perishable
# (e.g. "tata salt 1kg") immediately returns None rather than falling through
# to the category median.
_NAME_DAYS: dict[str, Optional[int]] = {
    item["name"].lower(): item["shelf_life_days"] for item in CATALOG
}

# All lower-cased names in insertion order — used for difflib lookups.
_ALL_NAMES: list[str] = list(_NAME_DAYS.keys())

# category → median of non-None shelf_life_days for that category.
# Categories where every SKU is non-perishable (None) won't appear here.
def _build_cat_days() -> dict[str, int]:
    buckets: dict[str, list[int]] = {}
    for item in CATALOG:
        sld = item["shelf_life_days"]
        if sld is not None:
            cat = item["category"]
            buckets.setdefault(cat, []).append(sld)
    return {cat: int(statistics.median(vals)) for cat, vals in buckets.items()}


_CAT_DAYS: dict[str, int] = _build_cat_days()


# ---------------------------------------------------------------------------
# Public API

def shelf_life_days_for(
    name: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[int]:
    """Return estimated shelf-life in days for a given SKU name and/or category.

    Resolution order:
    1. Exact case-insensitive match on CATALOG name.
    2. Fuzzy CATALOG name match (difflib, cutoff ~0.8), but only when the best
       match has a non-None shelf_life_days.
    3. Category median of non-None shelf_life_days.
    4. None (non-perishable or unknown).
    """
    # --- Step 1: exact name match (includes non-perishables → None) ----------
    if name:
        key = name.strip().lower()
        if key in _NAME_DAYS:
            return _NAME_DAYS[key]

        # --- Step 2: fuzzy name match (only useful when sld is non-None) -----
        matches = difflib.get_close_matches(key, _ALL_NAMES, n=1, cutoff=0.80)
        if matches:
            candidate_days = _NAME_DAYS[matches[0]]
            if candidate_days is not None:
                return candidate_days
            # Fuzzy hit was a non-perishable; fall through to category median.

    # --- Step 3: category median ---------------------------------------------
    if category:
        cat_key = category.strip()
        if cat_key in _CAT_DAYS:
            return _CAT_DAYS[cat_key]

    # --- Step 4: unknown / non-perishable ------------------------------------
    return None


def estimate_expiry(
    name: Optional[str] = None,
    category: Optional[str] = None,
    received: Optional[datetime.date] = None,
) -> tuple[Optional[str], bool]:
    """Estimate an expiry date from the challan receipt date.

    Parameters
    ----------
    name:
        SKU display name (as it appears on a challan / in CATALOG).
    category:
        Product category (e.g. ``"Dairy"``).  Used as a fallback when the
        name match yields no shelf-life data.
    received:
        Date the stock was received.  Defaults to ``datetime.date.today()``.

    Returns
    -------
    (expiry_iso, is_estimated)
        *expiry_iso* is an ISO-8601 date string (``YYYY-MM-DD``) or ``None``
        for non-perishables.  *is_estimated* is ``True`` iff a date was
        computed, ``False`` otherwise.
    """
    if received is None:
        received = datetime.date.today()

    sld = shelf_life_days_for(name=name, category=category)
    if sld is None:
        return (None, False)

    expiry = received + datetime.timedelta(days=sld)
    return (expiry.isoformat(), True)
