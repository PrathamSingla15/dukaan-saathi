"""Proactive / scheduled agents for Dukaan Saathi.

These are the "Saathi notices things for you" features the dashboard surfaces
without the shopkeeper asking: items about to expire, overdue *udhaar* with
ready-to-send WhatsApp drafts, and a heads-up to stock up before the next
festival.

Everything here is pure analytics over :mod:`dukaan.ops` plus *best-effort*
Hindi phrasing via :mod:`dukaan.llm`. Every LLM call is wrapped so the feature
still works (falling back to a fixed Hindi template) when the llama-server is
down. Nothing here loads a model or hits the network at import time.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import holidays

from dukaan import config, llm, ops


# ===================================================================== utilities
def _money(x: float) -> str:
    """Format an amount in rupees the way the rest of the app does."""
    x = float(x)
    return f"{config.CURRENCY}{x:,.0f}" if x.is_integer() else f"{config.CURRENCY}{x:,.2f}"


def _draft_or_template(prompt: str, system: str, fallback: str) -> str:
    """Ask the LLM to phrase something in Hindi; fall back to a fixed template.

    All proactive LLM use goes through here so a missing/unhealthy llama-server
    never breaks the dashboard — we just return ``fallback``.
    """
    try:
        text = llm.complete(prompt, system=system, temperature=0.4, max_tokens=160)
        text = (text or "").strip()
        return text or fallback
    except Exception:
        return fallback


# ===================================================================== festivals
# Generic nudge for a festival we list without a specific stock hint.
_GENERIC_HINT = "mithai, dry fruits, snacks aur gift packs"


@lru_cache(maxsize=None)
def _festivals_data() -> dict[str, Any]:
    """Load the festivals dataset once; returns an empty structure on failure."""
    path: Path = config.FESTIVALS_OVERRIDES_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"stock_hints": {}, "festivals": {}}


def _load_festivals(years: list[int]) -> list[dict]:
    """Festivals for the given years, each ``{"name", "date", "stock", "estimated"}``.

    Primary source is the verified vendored dataset (``festival_overrides.json``,
    2026-2030, dates checked against drikpanchang). EVERY listed festival is
    surfaced with its kirana stock hint — nothing is dropped. For any requested
    year the dataset does not cover (e.g. beyond 2030) we fall back to
    ``holidays.India`` (public + optional), keeping only entries that match a
    known stock hint so the calendar keeps working into the future.
    """
    data = _festivals_data()
    hints: dict[str, str] = data.get("stock_hints", {})
    catalog: dict[str, dict] = data.get("festivals", {})

    result: list[dict] = []
    seen: set[tuple[dt.date, str]] = set()
    covered: set[int] = set()
    want = set(years)

    def _add(name: str, date: dt.date, stock: str, estimated: bool = False) -> None:
        key = (date, name)
        if key in seen:
            return
        seen.add(key)
        result.append({"name": name, "date": date,
                       "stock": stock or _GENERIC_HINT, "estimated": estimated})

    # 1) verified vendored dataset — show every festival, hint or not
    for name, info in catalog.items():
        stock = hints.get(info.get("hint", ""), _GENERIC_HINT)
        estimated = bool(info.get("estimated"))
        for iso in info.get("dates", []):
            try:
                d = dt.date.fromisoformat(iso)
            except (TypeError, ValueError):
                continue
            if d.year in want:
                covered.add(d.year)
                _add(name, d, stock, estimated)

    # 2) fallback for any uncovered year (beyond the dataset): library holidays
    #    that match a stock hint (drops civic-only days like Gandhi Jayanti).
    for year in want - covered:
        try:
            india_holidays = holidays.India(years=year, categories=("public", "optional"))
        except Exception:
            india_holidays = {}
        for hdate, hname in india_holidays.items():
            for part in hname.split(";"):
                part_lower = part.strip().lower()
                for kw, hint in hints.items():
                    if kw in part_lower:
                        _add(part.strip(), hdate, hint)
                        break

    result.sort(key=lambda f: f["date"])
    return result


def _next_festival(today: dt.date, lookahead: int) -> tuple[dict | None, int]:
    """Nearest upcoming festival within ``lookahead`` days, and how far away."""
    years = [today.year, today.year + 1]
    best: dict | None = None
    best_days = -1
    for fest in _load_festivals(years):
        fdate = fest["date"]
        days = (fdate - today).days
        if 0 <= days <= lookahead and (best is None or days < best_days):
            best = fest
            best_days = days
    return best, best_days


# ================================================================ expiry watcher
def expiry_watcher() -> dict:
    """Items expiring within ``config.EXPIRY_WARN_DAYS`` days.

    Returns ``{"count", "items", "message"}`` where ``message`` is a friendly
    Hindi line listing each item with its days-left (or a reassuring line when
    nothing is close to expiring).
    """
    items = ops.expiring_soon()
    if not items:
        return {"count": 0, "items": [],
                "message": "Abhi koi saamaan jaldi expire nahi ho raha — sab theek hai. 👍"}

    lines = []
    for it in items:
        days = it.get("days_left")
        estimated_suffix = " (anumanit)" if it.get("is_estimated") else ""
        if days is None:
            when = ""
        elif days < 0:
            when = f" (expiry {abs(int(days))} din pehle nikal chuki!)"
        elif days == 0:
            when = " (aaj expire ho raha hai!)"
        else:
            when = f" ({int(days)} din me)"
        lines.append(f"• {it['name']} — qty {it['qty']}{when}{estimated_suffix}")

    head = f"⚠️ {len(items)} saamaan jaldi expire ho raha hai:"
    message = head + "\n" + "\n".join(lines) + "\nInhe pehle bech dein ya offer laga dein."
    return {"count": len(items), "items": items, "message": message}


# =============================================================== udhaar reminder
# Khata names carry a trailing "(locality)" tag the owner uses to tell customers
# apart (e.g. "Rakesh Sharma (Gali No. 4)"). That identifier is for the owner, not
# the customer, so we strip it before addressing anyone in a WhatsApp reminder.
_LOC_TAG_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _greeting_name(name: str) -> str:
    """The customer's name with the owner's private locality tag removed."""
    return _LOC_TAG_RE.sub("", name or "").strip() or (name or "")


def _reminder_prompt(name: str, balance: float, earliest_due: str | None) -> str:
    due = f" (due date {earliest_due})" if earliest_due else ""
    return (
        f"Customer ka naam: {name}. Baaki udhaar: {_money(balance)}{due}.\n"
        "Iske liye ek bahut polite, dostana 1-2 line ki Hindi WhatsApp reminder likho "
        "jisme naam aur baaki rakam ho, aur dukaan ki taraf se dhanyavaad ho. "
        "Sirf message likho, koi extra explanation nahi."
    )


_REMINDER_SYSTEM = (
    "Tum ek chhoti kirana dukaan chalate ho jo apne customers ko "
    "udhaar yaad dilane ke liye polite Hindi WhatsApp message bhejte ho.")


def draft_reminder(name: str, balance: float, phone: str | None = None,
                   earliest_due: str | None = None) -> dict:
    """One polite Hindi WhatsApp reminder draft for a single customer.

    LLM-written when the llama-server is up, else a fixed Hindi template. Returns
    one ``{"customer", "balance", "phone", "draft"}`` entry — the same shape used
    inside :func:`udhaar_reminder`.
    """
    bal = round(float(balance or 0), 2)
    clean = _greeting_name(name)  # address the customer by name, not the owner's locality tag
    fallback = (f"नमस्ते {clean} जी, आपका {_money(bal)} उधार बाकी है, "
                "कृपया सुविधा अनुसार दे दें। धन्यवाद — दुकान")
    draft = _draft_or_template(
        _reminder_prompt(clean, bal, earliest_due), _REMINDER_SYSTEM, fallback)
    return {"customer": name, "balance": bal, "phone": phone, "draft": draft}


def udhaar_reminder() -> dict:
    """Polite Hindi WhatsApp reminder drafts for every overdue udhaar.

    Returns ``{"count", "reminders": [{"customer", "balance", "phone", "draft"}],
    "message"}``. Each draft is LLM-written when possible, else a fixed template.
    """
    reminders: list[dict] = [
        draft_reminder(c["name"], c["balance"], c.get("phone"), c.get("earliest_due"))
        for c in ops.overdue_udhaar()
    ]

    if not reminders:
        message = "Koi overdue udhaar nahi hai — sab khaate time par hain. 👍"
    else:
        total = round(sum(r["balance"] for r in reminders), 2)
        message = (f"🔔 {len(reminders)} customer ka udhaar overdue hai "
                   f"(kul {_money(total)}). Reminder draft ready hain.")
    return {"count": len(reminders), "reminders": reminders, "message": message}


# ================================================================ festival nudge
def _festival_prompt(name: str, days_away: int, stock_hint: str,
                     short_items: list[dict]) -> str:
    when = "aaj" if days_away == 0 else f"{days_away} din me"
    short = ""
    if short_items:
        names = ", ".join(i["name"] for i in short_items[:6])
        short = f"\nDukaan me inka stock abhi kam hai: {names}."
    return (
        f"Tyohaar: {name}, {when} aane wala hai. "
        f"Is tyohaar par aam taur par ye saaman zyada bikta hai: {stock_hint}.{short}\n"
        "Dukaandaar ke liye ek chhoti, friendly Hindi line likho jo unhe yaad dilaye "
        "ki tyohaar se pehle ye saaman stock kar lein. Sirf wo line likho."
    )


def festival_nudge(today: dt.date | str | None = None) -> dict:
    """Heads-up to stock up before the next festival.

    Looks ``config.FESTIVAL_LOOKAHEAD_DAYS`` days ahead; if a festival is near it
    suggests what to stock (festival hint cross-referenced with currently low /
    slow-moving items). Returns ``{"festival", "days_away", "message"}`` with
    ``festival`` set to ``None`` when nothing is coming up soon.
    """
    if today is None:
        today = dt.date.today()
    elif isinstance(today, str):
        today = dt.date.fromisoformat(today)

    fest, days_away = _next_festival(today, config.FESTIVAL_LOOKAHEAD_DAYS)
    if fest is None:
        return {"festival": None, "days_away": None,
                "message": (f"Agle {config.FESTIVAL_LOOKAHEAD_DAYS} din me koi bada "
                            "tyohaar nahi hai. 👍")}

    name, stock_hint = fest["name"], fest["stock"]

    # Cross-ref festival demand with items that are low / not moving, so the
    # nudge points at concrete stock the shop should top up. Best-effort.
    short_items: list[dict] = []
    try:
        seen: set = set()
        for row in ops.low_stock() + ops.slow_movers():
            key = row.get("item_id")
            if key in seen:
                continue
            seen.add(key)
            short_items.append(row)
    except Exception:
        short_items = []

    when = "aaj" if days_away == 0 else (
        "kal" if days_away == 1 else f"{days_away} din me")
    fallback = (f"🎉 {name} {when} hai! Pehle se stock kar lein: {stock_hint}. "
                "Tyohaar par maang badhti hai.")
    if fest.get("estimated"):
        fallback += " (Tareekh chaand ke hisaab se 1 din aage-peeche ho sakti hai.)"
    system = ("Tum ek experienced kirana dukaan salahkaar ho jo dukaandaar ko "
              "tyohaar se pehle sahi saaman stock karne ki salah dete ho, Hindi me.")
    message = _draft_or_template(
        _festival_prompt(name, days_away, stock_hint, short_items), system, fallback)

    return {"festival": fest, "days_away": days_away, "message": message}


# ====================================================================== run all
def run_all() -> dict:
    """Run every proactive check — what the dashboard 'alerts' panel renders."""
    return {"expiry": expiry_watcher(),
            "udhaar": udhaar_reminder(),
            "festival": festival_nudge()}
