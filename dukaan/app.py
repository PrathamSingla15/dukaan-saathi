"""Gradio "Bahi-Khata" interface for Dukaan Saathi.

A warm, custom-designed ledger interface over the UI-agnostic backend seam. The
shopkeeper speaks / types / snaps a bill; Gemma figures out the intent and the
entry writes itself. The screen *is* the account book it replaces: cream paper,
a red margin rule, indigo ink, brass numerals.

Design notes
------------
- Almost nothing here looks like default Gradio. The look lives in
  ``assets/style.css``; the interaction glue (instant English⇄Hindi toggle and
  client-side page nav) lives in ``assets/head.html``. Both are loaded in
  :func:`main` via ``launch(css_paths=..., head_paths=...)`` (Gradio 6 moved
  these off the ``Blocks`` ctor).
- Display surfaces are rendered as custom HTML strings into ``gr.HTML``; only the
  true I/O primitives (audio / image / textbox / checkbox) stay Gradio, restyled.
- ``gr.HTML`` supports ``.click()`` in Gradio 6, so every button is a custom,
  bilingual HTML element wired either to the backend or to pure client-side JS.
- UI-only: this module imports just the seam (``session`` / ``onboarding`` /
  ``receiving``) plus ``proactive`` (reminder drafts) and ``config`` / ``db``.
  Heavy models load lazily inside those modules — nothing ML at import time.
- English is the default chrome; the toggle flips every label to Hindi instantly
  (client-side). The *assistant's replies* stay Hindi — that is the backend's
  voice today (a deliberate Hindi-first prompt choice), surfaced intentionally.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
import os
import re
import urllib.parse
import uuid
from pathlib import Path

import gradio as gr

from dukaan import config, db, i18n, onboarding, proactive, receiving, session

ASSETS = Path(__file__).resolve().parent / "assets"

_TAGLINE_HI = "बोलिए या बिल दिखाइए · स्टॉक, उधार और हिसाब सब अपने आप।"
_TAGLINE_EN = "Just speak, or show a bill · stock, credit & accounts keep themselves."
_ERROR_HI = "माफ़ कीजिए, कुछ गड़बड़ हो गई। थोड़ी देर बाद फिर कोशिश करें।"
_PLACEHOLDER = (
    "e.g. '10 Parle-G packet aaye, 5 rupaye wala' · "
    "'Sharma ji ne 200 ka udhaar liya' · 'aaj kitni bikri hui?'"
)


# ============================================================ tiny helpers
def T(en: str, hi: str) -> str:
    """Inline bilingual chrome: both spans emitted, CSS shows the active one."""
    return f'<span class="i18n-en">{en}</span><span class="i18n-hi">{hi}</span>'


def ic(name: str) -> str:
    """Inline a Phosphor icon (webfont loaded in head.html) — used instead of emojis."""
    return f'<i class="ph ph-{name}" aria-hidden="true"></i>'


def _esc(s) -> str:
    return _html.escape("" if s is None else str(s))


def _text(s) -> str:
    """Escape and keep line breaks for chat / message bodies."""
    return _esc(s).replace("\n", "<br>")


_MD = None


def _md(s) -> str:
    """Render a little markdown (bold / italic / lists / code) for bot replies.

    Uses markdown-it with ``html=False`` so any raw HTML in the model's text is
    escaped (safe); falls back to plain escaped text if the lib is unavailable.
    """
    if not s:
        return ""
    global _MD
    try:
        if _MD is None:
            from markdown_it import MarkdownIt
            # commonmark + GFM tables (commonmark preset omits them)
            _MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable("table")
        return _MD.render(str(s))
    except Exception:  # noqa: BLE001
        return _text(s)


def _grp(n: int) -> str:
    """Indian digit grouping: 219692 -> '2,19,692'."""
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, last3 = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{last3}"


def _money(x) -> str:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return "₹0"
    return ("−" if v < 0 else "") + "₹" + _grp(round(abs(v)))


def _rupee(x) -> str:
    """Money for a KPI — the ₹ gets the marigold accent class."""
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        v = 0
    sign = "−" if v < 0 else ""
    return f'{sign}<span class="rupee">₹</span>{_grp(round(abs(v)))}'


def _btn(label_html: str, *, kind: str = "", elem_id: str | None = None,
         attrs: str = "") -> gr.HTML:
    """A custom bilingual button as a clickable gr.HTML.

    Wired by the caller via ``.click()``, or — when ``attrs`` carries a client-side
    hook like ``data-page="talk"`` — handled purely in ``head.html``.
    """
    cls = "dk-btn" + (f" dk-btn--{kind}" if kind else "")
    html = f'<div class="{cls}"{(" " + attrs) if attrs else ""}>{label_html}</div>'
    return gr.HTML(
        html, elem_id=elem_id, elem_classes=["dk-raw", "dk-btnwrap"],
        apply_default_css=False, container=False, padding=False,
    )


def _panel(value: str = "", *, elem_id: str | None = None) -> gr.HTML:
    return gr.HTML(
        value, elem_id=elem_id, elem_classes=["dk-raw"],
        apply_default_css=False, container=False, padding=False,
    )


def _dot(overdue) -> str:
    return ('<span class="dk-dot dk-dot--red"></span>' if overdue
            else '<span class="dk-dot dk-dot--ok"></span>')


def _est_badge(flag, label: str = "~est") -> str:
    return f' <span class="dk-badge--muted">{label}</span>' if flag else ""


def _overdue_badge(overdue) -> str:
    return (' <span class="dk-badge--red">' + T("overdue", "बकाया") + "</span>") if overdue else ""


def _from_sub(it: dict) -> str:
    inp, res = it.get("input_name"), it.get("resolved_name")
    if inp and inp != res:
        return '<div class="cat">' + T("from", "से") + ": " + _esc(inp) + "</div>"
    return ""


def _action_badge(action) -> str:
    if action == "merge":
        return '<span class="dk-badge--ok">' + T("restock", "रीस्टॉक") + "</span>"
    return '<span class="dk-badge--brass">' + T("new", "नया") + "</span>"


def _days_chip(d) -> str:
    if d is None:
        return '<span class="dk-badge--muted">—</span>'
    try:
        d = int(d)
    except (TypeError, ValueError):
        return '<span class="dk-badge--muted">—</span>'
    if d < 0:
        return f'<span class="dk-badge--red">{T(f"{abs(d)}d ago", f"{abs(d)} दिन पहले")}</span>'
    if d == 0:
        return f'<span class="dk-badge--red">{T("today", "आज")}</span>'
    cls = "dk-badge--red" if d <= 3 else ("dk-badge--brass" if d <= 7 else "dk-badge--muted")
    return f'<span class="{cls}">{T(f"{d}d", f"{d} दिन")}</span>'


# ============================================================ render: chrome
def _mast_festival_chip(snap: dict | None) -> str:
    """A small festival-countdown chip for the masthead — only when one is near."""
    fest = (snap or {}).get("festival") or {}
    fobj = fest.get("festival") if isinstance(fest, dict) else None
    if not fobj:
        return ""
    away = fest.get("days_away")
    when = (T("today", "आज") if away == 0
            else T("tomorrow", "कल") if away == 1
            else T(f"in {away} days", f"{away} दिन में"))
    return (f'<span class="dk-mast__fest">{ic("confetti")} '
            f'{_esc(fobj.get("name"))} · {when}</span>')


def masthead_html(snap: dict | None = None) -> str:
    # The date sits at the top of the page, the way a real bahi-khata always opens.
    # Rendered once at page load (fine for a session); both languages emitted.
    en_full, hi_full = i18n.format_date_full(_dt.date.today())
    date_line = T(f"Account for {en_full}", f"{hi_full} का हिसाब")
    return f"""
<div class="dk-mast">
  <div class="dk-brand">
    <div class="dk-seal">{ic("storefront")}</div>
    <div>
      <div class="dk-title"><span class="en i18n-en">Dukaan Saathi</span><span class="hi i18n-hi">दुकान साथी</span></div>
      <div class="dk-sub">{T(_TAGLINE_EN, _TAGLINE_HI)}</div>
      <div class="dk-mast__date">{ic("calendar-blank")}<span class="dk-mast__dateval">{date_line}</span>{_mast_festival_chip(snap)}</div>
    </div>
  </div>
  <div class="dk-lang" role="group" aria-label="Language">
    <span data-lang-btn="en">EN</span><span data-lang-btn="hi">हिं</span>
  </div>
</div>"""


def nav_html() -> str:
    tabs = [
        ("today", ic("sun"), T("Today", "आज")),
        ("talk", ic("microphone"), T("Talk", "बातचीत")),
        ("khata", ic("notebook"), T("Credit", "खाता")),
        ("stock", ic("package"), T("Stock", "माल")),
        ("receive", ic("truck"), T("Receive", "सामान")),
        ("setup", ic("pencil-simple"), T("Setup", "नया खाता")),
    ]
    items = "".join(
        f'<div class="dk-tab" data-page="{pid}"><span class="ic">{ic}</span>{lab}</div>'
        for pid, ic, lab in tabs
    )
    return f'<div class="dk-nav">{items}</div>'


def _secthead(icon: str, en: str, hi: str, meta: str = "") -> str:
    m = f'<span class="meta">{meta}</span>' if meta else ""
    return (f'<div class="dk-secthead"><h2>{icon} {T(en, hi)}'
            f'<span class="dot"> ·</span></h2>{m}</div>')


def _page_date_meta(extra: str = "") -> str:
    """Today's date for a section-head meta slot (every page carries it, like a real
    register). ``extra`` appends a tag after the date (e.g. demo/live on Today)."""
    en, hi = i18n.format_date_short(_dt.date.today())
    base = f'{ic("calendar-blank")} {T(en, hi)}'
    return f"{base} · {extra}" if extra else base


# ============================================================ render: dashboard
def _offline_banner(snap: dict) -> str:
    if snap.get("server_up"):
        return ""
    return (f'<div class="dk-banner">{ic("warning")} '
            f'{T("Gemma (voice assistant) is offline — the dashboard below still works from your books. Start <code>scripts/serve_llm.sh</code> to chat.", "Gemma (आवाज़ सहायक) अभी बंद है — नीचे का हिसाब आपकी बही से चलता रहेगा। बात करने के लिए <code>scripts/serve_llm.sh</code> चलाएँ।")}</div>')


def dashboard_html(snap: dict | None) -> str:
    snap = snap or {}
    if snap.get("error") and not snap.get("stock_value"):
        return (f'<div class="dk-banner">{ic("warning")} '
                f'{T("Could not read the books", "हिसाब नहीं मिला")}: <code>{_esc(snap["error"])}</code></div>')

    sv = snap.get("stock_value") or {}
    td = snap.get("today") or {}
    exp = snap.get("expiring") or []
    low = snap.get("low_stock") or []
    ud = snap.get("udhaar") or {}
    slow = snap.get("slow_movers") or []
    fest = snap.get("festival") or {}

    cards: list[str] = []

    # --- stock value
    cards.append(f"""
<div class="dk-card col-4">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("wallet")}</span>
    <span class="dk-card__title">{T("Stock value", "स्टॉक की कीमत")}</span></div>
  <div class="dk-kpi dk-kpi--xl">{_rupee(sv.get("at_cost"))}</div>
  <div class="dk-kpi__sub">{T("at cost", "लागत पर")} · {T("MRP", "MRP")} {_money(sv.get("at_mrp"))}</div>
  <div class="dk-statrow">
    <div class="dk-stat"><div class="v ok">{_money(sv.get("potential_margin"))}</div><div class="k">{T("margin", "मुनाफ़ा")}</div></div>
    <div class="dk-stat"><div class="v">{_grp(sv.get("total_units", 0))}</div><div class="k">{T("units", "इकाई")}</div></div>
    <div class="dk-stat"><div class="v">{sv.get("item_count", 0)}</div><div class="k">{T("items", "चीज़ें")}</div></div>
  </div>
</div>""")

    # --- today's sales
    top = td.get("top_items") or []
    top_rows = "".join(
        f'<div class="dk-list__row"><span class="dk-list__rank">{i}</span>'
        f'<span class="nm">{_esc(t.get("name"))}</span>'
        f'<span class="amt">×{t.get("qty", 0)} · {_money(t.get("revenue"))}</span></div>'
        for i, t in enumerate(top[:3], 1)
    ) or f'<div class="dk-empty">{T("No sales logged yet today", "आज अभी कोई बिक्री नहीं")}</div>'
    cards.append(f"""
<div class="dk-card col-4">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("shopping-cart")}</span>
    <span class="dk-card__title">{T("Today's sales", "आज की बिक्री")}</span></div>
  <div class="dk-kpi dk-kpi--xl">{_rupee(td.get("revenue"))}</div>
  <div class="dk-kpi__sub">{td.get("units", 0)} {T("units", "इकाई")} · {td.get("num_sales", 0)} {T("sales", "बिक्री")}</div>
  <div class="spacer-8"></div>{top_rows}
</div>""")

    # --- udhaar
    custs = ud.get("customers") or []
    overdue_n = sum(1 for c in custs if c.get("overdue"))
    od_chip = (f'<span class="dk-badge--red dk-card__tag">{overdue_n} {T("overdue", "बकाया")}</span>'
               if overdue_n else "")
    ud_rows = "".join(
        f'<div class="dk-list__row" data-ask="{_esc(c.get("name"))} ka kitna baaki hai?">'
        f'{_dot(c.get("overdue"))}'
        f'<span class="nm">{_esc(c.get("name"))}</span>'
        f'<span class="amt {"red" if c.get("overdue") else ""}">{_money(c.get("balance"))}</span></div>'
        for c in custs[:5]
    ) or f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T("All settled", "सब चुकता")}</span></div>'
    cards.append(f"""
<div class="dk-card col-4">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("notebook")}</span>
    <span class="dk-card__title">{T("Pending credit", "बाकी उधार")}</span>{od_chip}</div>
  <div class="dk-kpi dk-kpi--xl">{_rupee(ud.get("total"))}</div>
  <div class="dk-kpi__sub">{ud.get("count", 0)} {T("customers owe you", "ग्राहक का उधार")}</div>
  <div class="spacer-8"></div>{ud_rows}
</div>""")

    # --- expiring soon
    exp_rows = "".join(
        f'<div class="dk-list__row" data-ask="{_esc(e.get("name"))} kitne din me expire ho raha hai?">'
        f'<span class="nm">{_esc(e.get("name"))}{_est_badge(e.get("is_estimated"))}</span>'
        f'<span class="sub">· {e.get("qty", 0)} {T("pcs", "नग")}</span>'
        f'<span class="amt">{_days_chip(e.get("days_left"))}</span></div>'
        for e in exp[:6]
    ) or f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T("Nothing expiring soon", "कुछ जल्दी एक्सपायर नहीं")}</span></div>'
    cards.append(f"""
<div class="dk-card col-7">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("hourglass-medium")}</span>
    <span class="dk-card__title">{T("Expiring soon", "जल्दी एक्सपायरी")}</span>
    <span class="dk-count dk-card__tag {"dk-count--red" if exp else ""}">{len(exp)}</span></div>
  {exp_rows}
</div>""")

    # --- low stock
    low_rows = "".join(
        f'<div class="dk-list__row" data-ask="{_esc(l.get("name"))} ka stock kitna hai?">'
        f'<span class="nm">{_esc(l.get("name"))}</span>'
        f'<span class="sub">· {_esc(l.get("category"))}</span>'
        f'<span class="amt red">{l.get("qty", 0)} / {l.get("reorder_level", 0)}</span></div>'
        for l in low[:6]
    ) or f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T("Stock levels healthy", "स्टॉक ठीक है")}</span></div>'
    cards.append(f"""
<div class="dk-card col-5">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("trend-down")}</span>
    <span class="dk-card__title">{T("Low stock", "कम स्टॉक")}</span>
    <span class="dk-count dk-card__tag {"dk-count--red" if low else ""}">{len(low)}</span></div>
  {low_rows}
</div>""")

    # --- slow movers
    slow_rows = "".join(
        f'<div class="dk-list__row" data-ask="{_esc(s.get("name"))} kyun nahi bik raha?">'
        f'<span class="nm">{_esc(s.get("name"))}</span>'
        f'<span class="sub">· {_esc(s.get("category"))}</span>'
        f'<span class="amt">{s.get("qty", 0)} {T("left", "बचे")}</span></div>'
        for s in slow[:6]
    ) or f'<div class="dk-empty">{T("Everything is moving", "सब बिक रहा है")}</div>'
    cards.append(f"""
<div class="dk-card col-7">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("chart-line-down")}</span>
    <span class="dk-card__title">{T("Not selling", "नहीं बिक रहा")}</span>
    <span class="dk-count dk-card__tag">{len(slow)}</span></div>
  {slow_rows}
  <div class="dk-hint">{T("Tap a row to ask the assistant why.", "कारण पूछने के लिए किसी पंक्ति पर टैप करें।")}</div>
</div>""")

    # --- festival nudge
    fobj = fest.get("festival") if isinstance(fest, dict) else None
    fmsg = (fest.get("message") if isinstance(fest, dict) else None) or ""
    if fobj:
        away = fest.get("days_away")
        when = (T("today", "आज") if away == 0 else T(f"in {away} days", f"{away} दिन में"))
        cards.append(f"""
<div class="dk-card dk-card--accent col-5">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("confetti")}</span>
    <span class="dk-card__title">{T("Festival coming", "आने वाला त्योहार")}</span>
    <span class="dk-badge--brass dk-card__tag">{_esc(fobj.get("name"))} · {when}</span></div>
  <div class="deva" style="font-size:15px;line-height:1.55;color:var(--ink)">{_text(fmsg)}</div>
</div>""")
    else:
        # No festival in the nudge window: instead of a near-empty card (which
        # leaves a dead block next to the full col-7 beside it), surface the next
        # upcoming festival so the card stays useful and the row stays balanced.
        nxt = fest.get("next") if isinstance(fest, dict) else None
        if nxt:
            try:
                den, dhi = i18n.format_month_day(_dt.date.fromisoformat(nxt.get("date")))
            except (TypeError, ValueError):
                den = dhi = ""
            na = nxt.get("days_away")
            na_txt = (T("today", "आज") if na == 0 else T("tomorrow", "कल") if na == 1
                      else T(f"in {na} days", f"{na} दिन में"))
            next_row = (
                f'<div class="dk-list__row" data-ask="{_esc(nxt.get("name"))} ke liye kya stock karna chahiye?">'
                f'<span class="nm">{_esc(nxt.get("name"))}{_est_badge(nxt.get("estimated"))}</span>'
                f'<span class="sub">· {T(den, dhi)}</span>'
                f'<span class="amt">{na_txt}</span></div>')
            cards.append(f"""
<div class="dk-card col-5">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("confetti")}</span>
    <span class="dk-card__title">{T("Festival watch", "त्योहार पर नज़र")}</span>
    <span class="dk-badge--muted dk-card__tag">{T("all clear", "अभी कुछ नहीं")}</span></div>
  <div class="dk-empty" style="padding:4px 0 2px">{T("Nothing in the next 30 days · next big day", "अगले 30 दिन में कुछ नहीं · अगला बड़ा दिन")}</div>
  {next_row}
  <div class="dk-hint">{T("We'll nudge you a few days before.", "कुछ दिन पहले याद दिला देंगे।")}</div>
</div>""")
        else:
            cards.append(f"""
<div class="dk-card col-5">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("confetti")}</span>
    <span class="dk-card__title">{T("Festival watch", "त्योहार पर नज़र")}</span></div>
  <div class="dk-empty deva">{_text(fmsg) or T("No big festival in the next 30 days.", "अगले 30 दिन में कोई बड़ा त्योहार नहीं।")}</div>
</div>""")

    return _offline_banner(snap) + f'<div class="dk-grid">{"".join(cards)}</div>'


def briefing_placeholder() -> str:
    return (f'<div class="dk-card dk-card--plain">'
            f'<div class="dk-card__head"><span class="dk-card__icon">{ic("sun-horizon")}</span>'
            f'<span class="dk-card__title">{T("Morning briefing", "सुबह का हाल")}</span></div>'
            f'<div class="muted">{T("Namaste! Your morning briefing is ready. Tap below to hear today\'s expiry, credit & festival news.", "नमस्ते! आपका सुबह का हाल तैयार है। आज की एक्सपायरी, उधार और त्योहार की खबर सुनने के लिए नीचे टैप करें।")}</div></div>')


def briefing_html(text: str) -> str:
    return (f'<div class="dk-card dk-card--plain">'
            f'<div class="dk-card__head"><span class="dk-card__icon">{ic("sun-horizon")}</span>'
            f'<span class="dk-card__title">{T("Morning briefing", "सुबह का हाल")}</span></div>'
            f'<div class="deva" style="font-size:16px;line-height:1.65;color:var(--ink)">{_text(text)}</div></div>')


# ============================================================ render: talk / chat
# Progress labels shown with the typing dots while the agent works (pre-answer).
_THINKING = T("Thinking", "सोच रहे हैं")
_STATUS = {
    "read": T("Checking the books", "बही से देख रहे हैं"),
    "write": T("Writing to the books", "बही में लिख रहे हैं"),
}


_INTENT = {
    "write": ("dk-badge--brass", ic("pencil-simple") + " " + T("recorded", "लिखा गया")),
    "sale": ("dk-badge--brass", ic("shopping-cart") + " " + T("sale", "बिक्री")),
    "udhaar": ("dk-badge--red", ic("notebook") + " " + T("credit", "उधार")),
    "restock": ("dk-badge--ok", ic("package") + " " + T("restock", "स्टॉक")),
    "lookup": ("dk-badge--muted", ic("magnifying-glass") + " " + T("lookup", "जानकारी")),
    "query": ("dk-badge--muted", ic("magnifying-glass") + " " + T("query", "सवाल")),
}


# Tappable starter questions on the empty Talk screen — each fills the composer and
# submits (head.html's data-ask handler). Label is short + bilingual; the data-ask
# value is the Hinglish the agent expects.
_WELCOME_CHIPS = (
    ("shopping-cart", "Today's sales", "आज की बिक्री", "aaj kitni bikri hui?"),
    ("notebook", "Top udhaar", "सबसे ज़्यादा उधार", "sabse zyada udhaar kiska hai?"),
    ("package", "Check stock", "स्टॉक देखें", "Parle-G ka stock kitna hai?"),
    ("hourglass-medium", "Expiring soon", "जल्दी एक्सपायरी", "kya kuch jaldi expire ho raha hai?"),
)

# A friendly, bilingual gloss for each tool the agent can call. We turn a turn's
# raw tool-call list into a small "how Saathi answered" trace under the reply, so
# the owner (and a judge) can see the agent actually planning + acting on the books
# rather than a black box — the visible side of the "Best Agent" capability.
_TOOL_TRACE = {
    "query_database":       ("magnifying-glass", "read the books", "बही पढ़ी"),
    "get_dashboard":        ("gauge", "checked today's summary", "आज का हाल देखा"),
    "get_item_detail":      ("package", "looked up the item", "सामान देखा"),
    "get_customer_dues":    ("notebook", "checked the udhaar", "उधार देखा"),
    "add_inventory_tool":   ("package", "prepared a stock entry", "स्टॉक तैयार किया"),
    "record_sale_tool":     ("shopping-cart", "prepared the sale", "बिक्री तैयार की"),
    "record_purchase_tool": ("truck", "prepared the purchase", "खरीद तैयार की"),
    "add_udhaar_tool":      ("notebook", "prepared the udhaar", "उधार तैयार किया"),
    "record_payment_tool":  ("hand-coins", "prepared the payment", "भुगतान तैयार किया"),
    "confirm_pending_tool": ("check", "saved it to the books", "बही में सेव किया"),
}


def _tool_trace_html(tools) -> str:
    """Render a turn's tool calls as a small 'what Saathi did' trace under the reply."""
    if not tools:
        return ""
    order: list[str] = []
    for t in tools:
        if t in _TOOL_TRACE and t not in order:
            order.append(t)
    if not order:
        return ""
    steps = "".join(
        f'<span class="dk-trace__step">{ic(_TOOL_TRACE[t][0])} {T(_TOOL_TRACE[t][1], _TOOL_TRACE[t][2])}</span>'
        for t in order
    )
    return (f'<div class="dk-trace"><span class="dk-trace__lab">{ic("path")} '
            f'{T("how Saathi answered", "साथी ने कैसे देखा")}</span>{steps}</div>')


def chat_html(history: list[dict], *, typing: bool = False, status: str = "") -> str:
    rows: list[str] = []
    for i, m in enumerate(history or []):
        if m.get("role") == "user":
            det = m.get("detected")
            chip = (f' · <span class="dk-detect">{T("Heard", "सुना")}: {_esc(det)}</span>'
                    if det else "")
            rows.append(
                f'<div class="dk-msg dk-msg--user"><div class="dk-msg__meta">'
                f'{T("You", "आप")}{chip}</div>'
                f'<div class="dk-msg__body">{_text(m.get("text"))}</div></div>')
        else:
            bc = ""
            it = m.get("intent")
            if it in _INTENT:
                cls, lab = _INTENT[it]
                bc = f'<span class="{cls}">{lab}</span>'
            err = " err" if m.get("err") else ""
            # per-message speaker: replies are silent by default; tap to hear one
            speak = (f'<span class="dk-speak" data-speak-idx="{i}" role="button" '
                     f'title="Listen / सुनें" aria-label="Listen">{ic("speaker-high")}</span>')
            rows.append(
                f'<div class="dk-msg dk-msg--bot"><div class="dk-msg__meta">'
                f'{ic("storefront")} {T("Saathi", "साथी")} {bc}'
                f'<span class="dk-msg__spacer"></span>{speak}</div>'
                f'<div class="dk-msg__body{err}"><div class="dk-md">{_md(m.get("text"))}</div></div>'
                f'{_tool_trace_html(m.get("tools"))}</div>')
    if typing:
        lbl = f'<span class="dk-typing__t">{status}</span>' if status else ""
        rows.append(f'<div class="dk-typing">{lbl}<i></i><i></i><i></i></div>')

    if not rows:
        chips = "".join(
            f'<span class="dk-suggest" data-ask="{_esc(q)}">{ic(icn)} {T(en, hi)}</span>'
            for icn, en, hi, q in _WELCOME_CHIPS
        )
        body = (f'<div class="dk-welcome"><div class="big">{ic("hand-waving")}</div>'
                f'<p>{T("Namaste! Tell me a sale, a credit, or a question · by voice, photo, or text.", "नमस्ते! बिक्री, उधार या सवाल बताइए · बोलकर, फ़ोटो से या टाइप करके।")}</p>'
                f'<div class="dk-suggests">{chips}</div></div>')
    else:
        body = "".join(rows)

    return (f'<div class="dk-chatwrap"><div class="dk-chathead">'
            f'<span class="dk-dot dk-dot--ok"></span>'
            f'<span class="t">{T("Conversation", "बातचीत")}</span></div>'
            f'<div class="dk-chat">{body}</div></div>')


# ============================================================ render: khata
def _khata_draft_block(r: dict) -> str:
    """The generated WhatsApp reminder, shown expanded right under its customer row."""
    phone = (f'<span class="ph">{ic("phone")} {_esc(r.get("phone"))}</span>'
             if r.get("phone") else "")
    return (f'<div class="dk-khata-draft">'
            f'<div class="dk-khata-draft__head"><span class="dk-wa">{ic("whatsapp-logo")}</span>'
            f'<span class="t">{T("WhatsApp reminder", "WhatsApp रिमाइंडर")}</span>{phone}</div>'
            f'<div class="dk-khata-draft__msg">{_text(r.get("draft"))}</div></div>')


def _khata_row(c: dict, draft: dict | None) -> str:
    """One ledger row. When ``draft`` targets this customer the reminder is appended
    expanded beneath the row and the row is flagged open (stays highlighted)."""
    is_open = bool(draft and (c.get("name") or "") == draft.get("customer"))
    cls = "dk-list__row dk-khata-row" + (" dk-row--open" if is_open else "")
    row = (f'<div class="{cls}" data-khata-row data-customer="{_esc(c.get("name"))}">'
           f'{_dot(c.get("overdue"))}'
           f'<span class="nm">{_esc(c.get("name"))}</span>'
           f'<span class="sub">{(ic("phone") + " " + _esc(c.get("phone"))) if c.get("phone") else ""}'
           f'{(" · " + T("due", "देय") + " " + _esc(c.get("earliest_due"))) if c.get("earliest_due") else ""}</span>'
           f'<span class="amt {"red" if c.get("overdue") else ""}">{_money(c.get("balance"))}'
           f'{_overdue_badge(c.get("overdue"))}</span>'
           f'<span class="dk-remind-btn" data-remind="{_esc(c.get("name"))}">'
           f'{ic("whatsapp-logo")} {T("Generate reminder", "रिमाइंडर बनाएँ")}</span></div>')
    return row + (_khata_draft_block(draft) if is_open else "")


def khata_html(snap: dict | None, draft: dict | None = None) -> str:
    snap = snap or {}
    ud = snap.get("udhaar") or {}
    custs = ud.get("customers") or []
    overdue_n = sum(1 for c in custs if c.get("overdue"))

    if not custs:
        rows = f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T("No credit pending — every khata is clear.", "कोई उधार बाकी नहीं — सब खाते साफ़।")}</span></div>'
    else:
        rows = "".join(_khata_row(c, draft) for c in custs)

    od_count_badge = (
        ' · <span class="dk-badge--red">' + str(overdue_n) + " " + T("overdue", "बकाया") + "</span>"
        if overdue_n else ""
    )
    head = f"""
<div class="dk-card col-12 dk-card--plain">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("notebook")}</span>
    <span class="dk-card__title">{T("Outstanding credit (udhaar)", "बाकी उधार")}</span>
    <span class="dk-card__tag">{_rupee(ud.get("total"))} · {ud.get("count", 0)} {T("customers", "ग्राहक")}
    {od_count_badge}</span></div>
  {rows}
  <div class="dk-hint">{T("Tap a customer to draft a WhatsApp reminder for them.", "किसी ग्राहक के लिए WhatsApp रिमाइंडर बनाने हेतु उस पर टैप करें।")}</div>
</div>"""
    return f'<div class="dk-grid">{head}</div>'


# ============================================================ render: stock
def _table(headers: list[str], rows_html: str, empty_en: str, empty_hi: str) -> str:
    if not rows_html:
        return f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T(empty_en, empty_hi)}</span></div>'
    head = "".join(f"<th>{h}</th>" for h in headers)
    return (f'<div class="dk-tablewrap"><table class="dk-table">'
            f'<thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody></table></div>')


def stock_html(snap: dict | None) -> str:
    snap = snap or {}
    sv = snap.get("stock_value") or {}
    exp = snap.get("expiring") or []
    low = snap.get("low_stock") or []
    slow = snap.get("slow_movers") or []

    summary = f"""
<div class="dk-card col-12">
  <div class="dk-card__head"><span class="dk-card__icon">{ic("package")}</span>
    <span class="dk-card__title">{T("Inventory at a glance", "स्टॉक एक नज़र में")}</span></div>
  <div class="dk-statrow">
    <div class="dk-stat"><div class="v">{_rupee(sv.get("at_cost"))}</div><div class="k">{T("at cost", "लागत")}</div></div>
    <div class="dk-stat"><div class="v">{_rupee(sv.get("at_mrp"))}</div><div class="k">{T("at MRP", "MRP")}</div></div>
    <div class="dk-stat"><div class="v ok">{_money(sv.get("potential_margin"))}</div><div class="k">{T("potential margin", "संभावित मुनाफ़ा")}</div></div>
    <div class="dk-stat"><div class="v">{_grp(sv.get("total_units", 0))}</div><div class="k">{T("units", "इकाई")}</div></div>
    <div class="dk-stat"><div class="v">{sv.get("item_count", 0)}</div><div class="k">{T("SKUs", "चीज़ें")}</div></div>
  </div>
</div>"""

    exp_rows = "".join(
        f'<tr data-ask="{_esc(e.get("name"))} ka stock kitna hai?"><td class="nm">{_esc(e.get("name"))}</td>'
        f'<td class="cat">{_esc(e.get("category"))}</td>'
        f'<td class="num">{e.get("qty", 0)}</td>'
        f'<td class="num">{_esc(e.get("expiry_date"))}{_est_badge(e.get("is_estimated"), "~")}</td>'
        f'<td>{_days_chip(e.get("days_left"))}</td></tr>'
        for e in exp[:14]
    )
    exp_tbl = _table(
        [T("Item", "चीज़"), T("Category", "श्रेणी"), T("Qty", "मात्रा"), T("Expiry", "एक्सपायरी"), T("Left", "बाकी")],
        exp_rows, "Nothing expiring soon", "कुछ जल्दी एक्सपायर नहीं")

    low_rows = "".join(
        f'<tr data-ask="{_esc(l.get("name"))} kitna mangwana chahiye?"><td class="nm">{_esc(l.get("name"))}</td>'
        f'<td class="cat">{_esc(l.get("category"))}</td>'
        f'<td class="num" style="color:var(--red);font-weight:600">{l.get("qty", 0)}</td>'
        f'<td class="num">{l.get("reorder_level", 0)}</td></tr>'
        for l in low
    )
    low_tbl = _table(
        [T("Item", "चीज़"), T("Category", "श्रेणी"), T("In stock", "स्टॉक"), T("Reorder at", "मंगाएँ")],
        low_rows, "Stock levels healthy", "स्टॉक ठीक है")

    slow_rows = "".join(
        f'<tr data-ask="{_esc(s.get("name"))} kyun nahi bik raha?"><td class="nm">{_esc(s.get("name"))}</td>'
        f'<td class="cat">{_esc(s.get("category"))}</td>'
        f'<td class="num">{s.get("qty", 0)}</td>'
        f'<td class="num">{_esc((s.get("last_sold") or "—")[:10])}</td></tr>'
        for s in slow[:14]
    )
    slow_tbl = _table(
        [T("Item", "चीज़"), T("Category", "श्रेणी"), T("Qty", "मात्रा"), T("Last sold", "आख़िरी बिक्री")],
        slow_rows, "Everything is moving", "सब बिक रहा है")

    return (f'<div class="dk-grid">{summary}'
            f'<div class="dk-card col-7"><div class="dk-card__head"><span class="dk-card__icon">{ic("hourglass-medium")}</span>'
            f'<span class="dk-card__title">{T("Expiring (FEFO)", "एक्सपायरी (FEFO)")}</span></div>{exp_tbl}</div>'
            f'<div class="dk-card col-5"><div class="dk-card__head"><span class="dk-card__icon">{ic("trend-down")}</span>'
            f'<span class="dk-card__title">{T("Low stock", "कम स्टॉक")}</span></div>{low_tbl}</div>'
            f'<div class="dk-card col-12"><div class="dk-card__head"><span class="dk-card__icon">{ic("chart-line-down")}</span>'
            f'<span class="dk-card__title">{T("Slow movers", "धीमे बिकने वाले")}</span></div>{slow_tbl}</div>'
            f'</div>')


# ============================================================ render: receive
def receive_intro() -> str:
    return (f'<div class="dk-card dk-card--plain"><div class="dk-card__head">'
            f'<span class="dk-card__icon">{ic("truck")}</span>'
            f'<span class="dk-card__title">{T("Receive from a challan", "चालान से सामान लें")}</span></div>'
            f'<div class="muted">{T("Snap a supplier bill. The assistant reads the line items, matches them to your stock (restock vs. new), and estimates expiry. Review, then add to inventory in one tap.", "सप्लायर का बिल फ़ोटो लें। सहायक हर चीज़ पढ़कर आपके स्टॉक से मिलाता है (रीस्टॉक या नया) और एक्सपायरी का अनुमान लगाता है। जाँच कर एक टैप में स्टॉक में जोड़ें।")}</div></div>')


def _receive_hint() -> str:
    """Small 'next step' cue shown in the result panel before a bill is read."""
    return (f'<div class="dk-hint">{ic("arrow-up")} '
            f'{T("Upload the bill photo above, then tap “Read the bill”.", "ऊपर बिल की फ़ोटो डालें, फिर “बिल पढ़ें” दबाएँ।")}</div>')


def receive_preview_html(preview: dict | None) -> str:
    preview = preview or {}
    if not preview.get("ok"):
        msg = preview.get("message") or T("Photo unclear — please re-upload.", "फ़ोटो साफ़ नहीं — दोबारा भेजें।")
        return (f'<div class="dk-card dk-card--plain"><div class="dk-banner">'
                f'{ic("camera")} {_text(msg)}</div></div>')
    items = preview.get("items") or []
    rows = "".join(
        f'<tr><td class="nm">{_esc(it.get("resolved_name"))}'
        f'{_from_sub(it)}</td>'
        f'<td class="num">{it.get("qty", 0)}</td>'
        f'<td class="num">{_money(it.get("rate")) if it.get("rate") is not None else "—"}</td>'
        f'<td class="num">{_money(it.get("mrp")) if it.get("mrp") is not None else "—"}</td>'
        f'<td class="num">{_esc(it.get("estimated_expiry") or "—")}{_est_badge(it.get("is_estimated"), "~")}</td>'
        f'<td>{_action_badge(it.get("action"))}</td></tr>'
        for it in items
    )
    tbl = _table(
        [T("Item", "चीज़"), T("Qty", "मात्रा"), T("Rate", "भाव"), T("MRP", "MRP"), T("Expiry", "एक्सपायरी"), T("Action", "क्रिया")],
        rows, "No line items found", "कोई चीज़ नहीं मिली")
    sup = preview.get("supplier")
    return (f'<div class="dk-card"><div class="dk-card__head"><span class="dk-card__icon">{ic("clipboard-text")}</span>'
            f'<span class="dk-card__title">{T("Review before adding", "जोड़ने से पहले जाँचें")}</span>'
            f'<span class="dk-card__tag">{(ic("storefront") + " " + _esc(sup) + " · ") if sup else ""}'
            f'{len(items)} {T("items", "चीज़ें")} · {_money(preview.get("total_cost"))}</span></div>'
            f'{tbl}<div class="dk-hint deva">{_text(preview.get("message"))}</div></div>')


def receive_result_html(result: dict | None) -> str:
    result = result or {}
    n = len(result.get("received") or [])
    failed = result.get("failed") or []
    stamp = (f'<span class="dk-stamp">{ic("check")} {T("Added to stock", "स्टॉक में जोड़ा")}</span>'
             if not failed else
             f'<span class="dk-stamp dk-stamp--red">! {T("Partly added", "कुछ जुड़ा")}</span>')
    fail_note = (f'<div class="dk-banner">{ic("warning")} {len(failed)} {T("rows could not be saved — please re-check.", "पंक्तियाँ सेव नहीं हुईं — दोबारा देखें।")}</div>'
                 if failed else "")
    return (f'<div class="dk-card dk-card--plain">{fail_note}'
            f'<div class="dk-card__head">{stamp}'
            f'<span class="dk-card__tag">{n} {T("items", "चीज़ें")} · {_money(result.get("total_cost"))}</span></div>'
            f'<div class="deva" style="margin-top:8px;color:var(--ink)">{_text(result.get("message_hi"))}</div></div>')


# ============================================================ render: onboarding
_ONB_STEPS = [
    ("Profile", "प्रोफ़ाइल"), ("Stock", "सामान"), ("Khata", "खाता"),
    ("Verify", "जाँच"), ("Done", "हो गया"),
]


def _stepper(idx: int) -> str:
    parts = []
    for i, (en, hi) in enumerate(_ONB_STEPS):
        state = "dk-step--done" if i < idx else ("dk-step--active" if i == idx else "")
        num = ic("check") if i < idx else str(i + 1)
        line = '<div class="dk-step__line"></div>' if i < len(_ONB_STEPS) - 1 else ""
        parts.append(
            f'<div class="dk-step {state}"><div class="dk-step__num">{num}</div>'
            f'<div class="dk-step__lab">{T(en, hi)}</div>{line}</div>')
    return f'<div class="dk-stepper">{"".join(parts)}</div>'


def onboarding_intro() -> str:
    return (f'<div class="dk-draftcard"><div class="dk-card__head"><span class="dk-card__icon">{ic("pencil-simple")}</span>'
            f'<span class="dk-card__title">{T("Open your own book", "अपना खाता खोलें")}</span></div>'
            f'<p class="muted" style="margin:6px 0 0">{T("Replace the demo data with your real shop — add your stock by voice, photo, or by hand, snap your khata, review, and save. Takes a few minutes.", "डेमो डेटा की जगह अपनी असली दुकान डालें — सामान बोलकर/फ़ोटो से/हाथ से जोड़ें, अपना खाता फ़ोटो लें, जाँचें और सेव करें। कुछ ही मिनट।")}</p></div>')


def onboarding_html(view: dict | None) -> str:
    view = view or {}
    idx = view.get("step_index", 0)
    state = view.get("state")

    if "drafts" in view:
        items = view["drafts"].get("items", [])
        custs = view["drafts"].get("customers", [])
    else:
        items = view.get("items", [])
        custs = view.get("customers", [])

    blocks = [_stepper(idx)]

    needs = view.get("needs")
    if needs and view.get("prompt"):
        blocks.append(f'<div class="dk-banner">{ic("repeat")} {_text(view["prompt"])}</div>')
    elif view.get("ok") is False and view.get("message"):
        blocks.append(f'<div class="dk-banner">{ic("warning")} {_text(view["message"])}</div>')

    # profile summary once set
    prof = view.get("profile") if isinstance(view.get("profile"), dict) else None
    if prof and (prof.get("shop_name") or prof.get("owner_name")):
        blocks.append(
            f'<div class="dk-card dk-card--plain"><span class="dk-badge--ok">{ic("check")} {T("Profile", "प्रोफ़ाइल")}</span> '
            f'<b>{_esc(prof.get("shop_name"))}</b> · {_esc(prof.get("owner_name"))} '
            f'<span class="muted">({_esc(prof.get("language") or "hi")})</span></div>')

    # item drafts
    if items:
        rows = "".join(
            f'<div class="dk-list__row"><span class="nm">{_esc(it.get("name"))}</span>'
            f'<span class="sub">{_esc(it.get("category") or "")} '
            f'<span class="dk-badge--{"ok" if it.get("confidence") == "high" else "muted"}">{_esc(it.get("source"))}</span></span>'
            f'<span class="amt">×{it.get("qty", 0)}</span></div>'
            for it in items)
        blocks.append(
            f'<div class="dk-card"><div class="dk-card__head"><span class="dk-card__icon">{ic("package")}</span>'
            f'<span class="dk-card__title">{T("Stock added", "जोड़ा सामान")}</span>'
            f'<span class="dk-count dk-card__tag">{len(items)}</span></div>{rows}</div>')

    # customer drafts
    if custs:
        rows = "".join(
            f'<div class="dk-list__row"><span class="nm">{_esc(c.get("name"))}</span>'
            f'<span class="sub">{(ic("phone") + " " + _esc(c.get("phone"))) if c.get("phone") else ""}</span>'
            f'<span class="amt">{_money(c.get("opening_balance"))}</span></div>'
            for c in custs)
        blocks.append(
            f'<div class="dk-card"><div class="dk-card__head"><span class="dk-card__icon">{ic("notebook")}</span>'
            f'<span class="dk-card__title">{T("Khata customers", "खाता ग्राहक")}</span>'
            f'<span class="dk-count dk-card__tag">{len(custs)}</span></div>{rows}</div>')

    # verify totals
    if state == "verify":
        tot = view.get("totals") or {}
        blocks.append(
            f'<div class="dk-totals">'
            f'<div class="dk-stat"><div class="v">{tot.get("item_count", 0)}</div><div class="k">{T("items", "चीज़ें")}</div></div>'
            f'<div class="dk-stat"><div class="v">{_grp(tot.get("total_units", 0))}</div><div class="k">{T("units", "इकाई")}</div></div>'
            f'<div class="dk-stat"><div class="v">{tot.get("customer_count", 0)}</div><div class="k">{T("customers", "ग्राहक")}</div></div>'
            f'<div class="dk-stat"><div class="v">{_money(tot.get("opening_balance_total"))}</div><div class="k">{T("opening credit", "शुरुआती उधार")}</div></div>'
            f'</div>'
            f'<div class="dk-banner dk-banner--info">{T("Looks right? Press Confirm & Save to make this your real shop.", "सब ठीक है? इसे अपनी असली दुकान बनाने के लिए ‘पक्का करें’ दबाएँ।")}</div>')

    if not items and not custs and state != "verify":
        blocks.append(f'<div class="dk-empty">{T("Add your first items below — by hand, voice, or photo.", "नीचे अपनी पहली चीज़ें जोड़ें — हाथ से, बोलकर या फ़ोटो से।")}</div>')

    return "".join(blocks)


# ============================================================ event handlers
# Cleared value for the multimodal composer (text box + attachments empty).
_EMPTY_MM = {"text": "", "files": []}

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_AUD_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".aac")


def _parse_composer(mm) -> tuple[str, object, object]:
    """Split a MultimodalTextbox value ``{text, files}`` into (text, image, audio).

    Mic recordings and uploaded images arrive as file paths; we route by
    extension — image → PIL.Image, audio → ``(sample_rate, ndarray)`` — exactly
    what :func:`session.handle_turn` expects. Unknown file types are ignored.
    """
    mm = mm or {}
    text = (mm.get("text") or "").strip()
    image = audio = None
    for f in (mm.get("files") or []):
        path = f.get("path") if isinstance(f, dict) else f
        if not path:
            continue
        ext = os.path.splitext(str(path))[1].lower()
        if ext in _IMG_EXT and image is None:
            try:
                from PIL import Image
                image = Image.open(path).convert("RGB")
            except Exception:  # noqa: BLE001 — bad upload shouldn't crash the turn
                pass
        elif ext in _AUD_EXT and audio is None:
            try:
                import soundfile as sf
                data, sr = sf.read(path, dtype="float32")
                audio = (sr, data)
            except Exception:  # noqa: BLE001
                pass
    return text, image, audio


def respond(mm, history, thread_id):
    """One shopkeeper turn from the chat composer (text / voice / image).

    Replies are SILENT by default (no inline TTS) — each bot bubble carries a
    speaker button to play it on demand. Returns
    ``(chat, audio_out, dashboard, confirm_row, composer, history)``.
    """
    history = list(history or [])
    text, image, audio = _parse_composer(mm)
    if text:
        shown = text
    elif audio is not None:
        shown = "Voice note + photo" if image is not None else "Voice note"
    elif image is not None:
        shown = "Photo"
    else:
        shown = "…"
    ui = len(history)
    history.append({"role": "user", "text": shown})

    # optimistic: show the question + a "Thinking…" indicator and clear the composer
    yield (chat_html(history, typing=True, status=_THINKING), gr.update(), gr.update(),
           gr.update(visible=False), _EMPTY_MM, history)

    # The bot bubble is added only once real answer text arrives — until then we
    # keep the dots + a status label ("Checking the books…") so the empty bubble
    # never flashes.
    bot_added = False
    bi = None
    dash_out = gr.update()
    pend_vis = False
    last_len = 0
    for tr in session.handle_turn_stream(audio=audio, text=text or None, image=image,
                                         thread_id=thread_id, tts=False):
        if tr.user_text:
            history[ui]["text"] = tr.user_text
        if tr.detected_language:
            history[ui]["detected"] = tr.detected_language

        txt = tr.reply_text or ""
        if not txt.strip():
            # pre-answer phase: dots + what the agent is doing (tool/DB call)
            yield (chat_html(history, typing=True, status=_STATUS.get(tr.status, _THINKING)),
                   None, gr.update(), gr.update(visible=False), _EMPTY_MM, history)
            continue

        if not bot_added:
            bi = len(history)
            history.append({"role": "bot", "text": txt, "intent": tr.intent_badge})
            bot_added = True
        else:
            history[bi]["text"] = txt
            history[bi]["intent"] = tr.intent_badge
        history[bi]["err"] = bool(tr.error)
        if tr.tool_calls:
            history[bi]["tools"] = tr.tool_calls

        is_final = bool(tr.dashboard_snapshot or tr.pending_confirmation or tr.error)
        if is_final or (len(txt) - last_len) >= 5:
            if tr.dashboard_snapshot:
                dash_out = dashboard_html(tr.dashboard_snapshot)
            pend_vis = bool(tr.pending_confirmation)
            last_len = len(txt)
            yield (chat_html(history), None, dash_out,
                   gr.update(visible=pend_vis), _EMPTY_MM, history)


def confirm(answer, thread_id, history):
    history = list(history or [])
    r = session.confirm_pending(answer, thread_id=thread_id, tts=False)
    history.append({"role": "bot", "text": r.reply_text, "intent": "write"})
    dash = dashboard_html(r.dashboard_snapshot or session.dashboard_snapshot_struct())
    return chat_html(history), None, dash, gr.update(visible=False), history


def speak(idx, history):
    """On-demand TTS for one bot message (by chat index) → plays in audio_out.

    ``idx`` arrives as ``"<index>|<nonce>"`` (the nonce guarantees the textbox
    value changes on every click, so repeat-listens still fire the event).
    """
    try:
        text = (history or [])[int(str(idx).split("|")[0])].get("text", "")
    except (TypeError, ValueError, IndexError, AttributeError):
        return None
    return session.speak(text) if (text and text.strip()) else None


def refresh_dashboard():
    return dashboard_html(session.dashboard_snapshot_struct())


def load_briefing():
    try:
        return briefing_html(session.morning_briefing(tts=False)["text"])
    except Exception as e:  # noqa: BLE001 — best-effort
        return briefing_html(f"{_ERROR_HI}\n{e}")


def refresh_khata():
    return khata_html(session.dashboard_snapshot_struct())


def remind_one(token):
    """Draft a reminder for the customer in ``token`` and re-render the ledger with it
    expanded directly under that customer's row.

    ``token`` arrives as ``"<name>|<nonce>"`` from a khata row's reminder pill (the
    nonce guarantees the bridge textbox value changes on every click, so a repeat tap
    still fires the event). Balance / phone / due date are looked up in the live
    snapshot, so the DOM only ever carries the customer's name.
    """
    name = str(token or "").split("|")[0].strip()
    snap = session.dashboard_snapshot_struct()
    if not name:
        return khata_html(snap)
    try:
        custs = (snap.get("udhaar") or {}).get("customers") or []
        c = next((x for x in custs if (x.get("name") or "") == name), None)
        if c is None:
            return khata_html(snap)
        one = proactive.draft_reminder(
            name, c.get("balance") or 0, c.get("phone"), c.get("earliest_due"))
        return khata_html(snap, draft=one)
    except Exception:  # noqa: BLE001 — never break the ledger view
        return khata_html(snap)


def refresh_stock():
    return stock_html(session.dashboard_snapshot_struct())


def receive_parse(image):
    if image is None:
        return _receive_hint(), None
    preview = receiving.stage_receive(image=image)
    return receive_preview_html(preview), (preview if preview.get("ok") else None)


def receive_commit(preview):
    if not preview or not preview.get("items"):
        return (_receive_hint(), None,
                refresh_dashboard(), refresh_stock())
    result = receiving.commit_receive(preview["items"], supplier=preview.get("supplier"))
    return (receive_result_html(result), None,
            refresh_dashboard(), refresh_stock())


# ---- onboarding handlers
def onb_begin():
    return onboarding_html(onboarding.start_onboarding(resume=True))


def onb_profile(owner, shop, lang):
    return onboarding_html(onboarding.set_profile(owner or "", shop or "", lang or "hi"))


def onb_manual(name, qty, cat):
    if not (name or "").strip():
        return onboarding_html(onboarding.start_onboarding(resume=True)), "", 1, ""
    view = onboarding.add_inventory_item_manual(name, qty or 1, cat or "")
    return onboarding_html(view), "", 1, ""


def onb_voice(audio):
    if audio is None:
        return onboarding_html(onboarding.start_onboarding(resume=True)), None
    return onboarding_html(onboarding.capture_inventory_voice(audio)), None


def onb_photo(image):
    if image is None:
        return onboarding_html(onboarding.start_onboarding(resume=True)), None
    return onboarding_html(onboarding.capture_inventory_photo(image)), None


def onb_khata(image):
    if image is None:
        return onboarding_html(onboarding.start_onboarding(resume=True)), None
    return onboarding_html(onboarding.capture_khata_photo(image)), None


def onb_verify():
    return onboarding_html(onboarding.advance_to_verify())


def onb_commit():
    res = onboarding.confirm_commit()
    if res.get("ok"):
        summ = res.get("summary") or {}
        it_n, cu_n = summ.get("items", 0), summ.get("customers", 0)
        hint = T(
            f"{it_n} items and {cu_n} customers saved. Reload the page to see your real shop on every tab.",
            f"{it_n} चीज़ें और {cu_n} ग्राहक सेव हुए। हर टैब पर अपनी असली दुकान देखने के लिए पेज दोबारा खोलें।",
        )
        done = (f'<div class="dk-draftcard">{_stepper(4)}'
                f'<div class="dk-empty"><span class="dk-stamp">{ic("check")} {T("Saved!", "सेव हो गया!")}</span></div>'
                f'<div class="deva" style="font-size:16px;color:var(--ink);margin-top:8px">{_text(res.get("message_hi"))}</div>'
                f'<div class="dk-hint">{hint}</div></div>')
        return done
    return onboarding_html(res)


def onb_abort():
    onboarding.abort_onboarding(keep_demo=True)
    return onboarding_intro()


# ============================================================ build UI
def build_ui() -> gr.Blocks:
    """Construct the single-page ledger app (no models touched here)."""
    initial = session.dashboard_snapshot_struct()

    with gr.Blocks(title="Dukaan Saathi", fill_width=True,
                   analytics_enabled=False) as demo:
        thread_id = gr.State(lambda: uuid.uuid4().hex)
        history = gr.State([])
        preview = gr.State(None)

        with gr.Column(elem_classes=["dk-shell"]):
            _panel(masthead_html(initial))
            _panel(nav_html())

            # ---------------------------------------------------------- TODAY
            with gr.Column(elem_id="page-today", elem_classes=["dk-page", "dk-page--active"]):
                _panel(_secthead(ic("sun"), "Today's account", "आज का हिसाब",
                                 meta=_page_date_meta(T("demo data", "डेमो डेटा")
                                                      if db.data_mode() == "demo" else T("live", "लाइव"))))
                briefing = _panel(briefing_placeholder())
                with gr.Row(elem_classes=["dk-btnrow"]):
                    brief_btn = _btn(ic("speaker-high") + " " + T("Generate morning briefing", "सुबह का हाल बनाएँ"), kind="gold")
                    _btn(ic("chats-circle") + " " + T("Chat to agent", "साथी से बात करें"),
                         kind="primary", attrs='data-page="talk"')
                _panel('<div class="spacer-16"></div>')
                dash = _panel(dashboard_html(initial))
                _panel('<div class="spacer-16"></div>')
                refresh_btn = _btn(ic("arrows-clockwise") + " " + T("Refresh", "ताज़ा करें"), kind="ghost")

            # ---------------------------------------------------------- TALK
            with gr.Column(elem_id="page-talk", elem_classes=["dk-page"]):
                _panel(_secthead(ic("microphone"), "Talk to your Saathi", "साथी से बात करें",
                                 meta=_page_date_meta()))
                chat = _panel(chat_html([]), elem_id="dk-chat-panel")
                with gr.Row(visible=False, elem_classes=["dk-btnrow"]) as confirm_row:
                    haan_btn = _btn(ic("check") + " " + T("Yes", "हाँ"), kind="primary")
                    nahi_btn = _btn(ic("x") + " " + T("No", "नहीं"), kind="ghost")
                # One chat composer bar: type, speak (mic), attach a bill photo, send.
                # MultimodalTextbox keeps Gradio's own (working) controls — we only
                # frame it; we never reset its internals (that broke the old inputs).
                _panel(f'<div class="dk-inlabel">{ic("chat-circle-dots")} '
                       f'{T("Speak, type, or snap a bill", "बोलें, टाइप करें, या बिल की फ़ोटो दिखाएँ")}</div>')
                composer = gr.MultimodalTextbox(
                    sources=["microphone", "upload"], file_count="multiple",
                    show_label=False, placeholder=_PLACEHOLDER, submit_btn=True,
                    elem_id="dk-composer", elem_classes=["dk-composer"],
                )
                _panel(f'<div class="dk-hint">{ic("speaker-high")} '
                       f'{T("Replies are silent · tap the speaker on any reply to hear it.", "जवाब बिना आवाज़ के आते हैं · सुनने के लिए किसी जवाब पर स्पीकर दबाएँ।")}</div>')
                audio_out = gr.Audio(autoplay=True, show_label=False, elem_id="dk-audio-out")
                # hidden bridge: a message's speaker icon → JS writes "<idx>|<nonce>"
                # here and fires its input event → speak() synthesizes just that
                # message → audio_out. NB: CSS-hidden (not visible=False) — Gradio 6
                # drops visible=False nodes from the DOM, so JS couldn't reach them.
                speak_idx = gr.Textbox(show_label=False, container=False,
                                       elem_id="dk-speak-idx", elem_classes=["dk-hidden"])
                speak_btn = gr.Button("speak", elem_id="dk-speak-btn", elem_classes=["dk-hidden"])

            # ---------------------------------------------------------- KHATA
            with gr.Column(elem_id="page-khata", elem_classes=["dk-page"]):
                _panel(_secthead(ic("notebook"), "Credit ledger", "उधार बही",
                                 meta=_page_date_meta()))
                khata = _panel(khata_html(initial))
                # hidden bridge (mirrors the speaker bridge): JS writes "<name>|<nonce>" into the
                # textbox and clicks the button → remind_one() re-renders the ledger with the
                # draft expanded under that customer's row.
                remind_name = gr.Textbox(show_label=False, container=False,
                                         elem_id="dk-remind-name", elem_classes=["dk-hidden"])
                remind_btn = gr.Button("remind", elem_id="dk-remind-btn", elem_classes=["dk-hidden"])

            # ---------------------------------------------------------- STOCK
            with gr.Column(elem_id="page-stock", elem_classes=["dk-page"]):
                _panel(_secthead(ic("package"), "Stock & expiry", "स्टॉक और एक्सपायरी",
                                 meta=_page_date_meta()))
                stock = _panel(stock_html(initial))
                _panel('<div class="spacer-16"></div>')
                stock_refresh = _btn(ic("arrows-clockwise") + " " + T("Refresh", "ताज़ा करें"), kind="ghost")

            # ---------------------------------------------------------- RECEIVE
            with gr.Column(elem_id="page-receive", elem_classes=["dk-page"]):
                _panel(_secthead(ic("truck"), "Receive a delivery", "सामान आया",
                                 meta=_page_date_meta()))
                _panel(receive_intro())   # explainer first — natural reading order
                with gr.Group(elem_classes=["dk-inputcard"]):
                    _panel(f'<div class="dk-inlabel">{ic("camera")} {T("Photo of the challan / bill", "चालान / बिल की फ़ोटो")}</div>')
                    rcv_img = gr.Image(sources=["upload", "webcam"], type="pil",
                                       show_label=False, elem_classes=["dk-input"])
                with gr.Row(elem_classes=["dk-btnrow"]):
                    rcv_parse = _btn(ic("magnifying-glass") + " " + T("Read the bill", "बिल पढ़ें"), kind="primary")
                    rcv_commit = _btn(ic("check") + " " + T("Add to stock", "स्टॉक में डालें"), kind="gold")
                _panel('<div class="spacer-8"></div>')
                rcv_view = _panel(_receive_hint())   # preview / result lands here, below the buttons

            # ---------------------------------------------------------- SETUP
            with gr.Column(elem_id="page-setup", elem_classes=["dk-page"]):
                _panel(_secthead(ic("pencil-simple"), "Set up your shop", "अपनी दुकान सेट करें",
                                 meta=_page_date_meta()))
                onb_view = _panel(onboarding_intro())
                begin_btn = _btn(ic("pencil-simple") + " " + T("Begin setup", "शुरू करें"), kind="primary")
                _panel('<div class="spacer-16"></div>')
                with gr.Group(elem_classes=["dk-inputcard"]):
                    _panel(f'<div class="dk-inlabel">1 · {T("Shop profile", "दुकान प्रोफ़ाइल")}</div>')
                    with gr.Row():
                        onb_owner = gr.Textbox(show_label=False, placeholder="Owner name · दुकानदार का नाम",
                                               elem_classes=["dk-input"])
                        onb_shop = gr.Textbox(show_label=False, placeholder="Shop name · दुकान का नाम",
                                              elem_classes=["dk-input"])
                        onb_lang = gr.Textbox(show_label=False, value="hi", placeholder="lang",
                                              scale=0, elem_classes=["dk-input"])
                    onb_save = _btn(ic("floppy-disk") + " " + T("Save profile", "प्रोफ़ाइल सेव करें"), kind="ghost")
                with gr.Group(elem_classes=["dk-inputcard"]):
                    _panel(f'<div class="dk-inlabel">2 · {T("Add stock", "सामान जोड़ें")}</div>')
                    with gr.Row():
                        onb_name = gr.Textbox(show_label=False, placeholder="Item · चीज़", elem_classes=["dk-input"])
                        onb_qty = gr.Number(value=1, show_label=False, scale=0, elem_classes=["dk-input"])
                        onb_cat = gr.Textbox(show_label=False, placeholder="Category · श्रेणी", elem_classes=["dk-input"])
                    onb_add = _btn(ic("plus") + " " + T("Add by hand", "हाथ से जोड़ें"), kind="ghost")
                    _panel('<div class="spacer-8"></div>')
                    with gr.Row():
                        with gr.Column():
                            _panel(f'<div class="dk-inlabel">{ic("microphone")} {T("…or say your stock", "…या सामान बोलें")}</div>')
                            onb_audio = gr.Audio(sources=["microphone", "upload"], type="numpy",
                                                 show_label=False, elem_classes=["dk-input"])
                            onb_voice_btn = _btn(ic("microphone") + " " + T("Add from voice", "बोलकर जोड़ें"), kind="ghost")
                        with gr.Column():
                            _panel(f'<div class="dk-inlabel">{ic("image")} {T("…or a shelf photo", "…या शेल्फ़ की फ़ोटो")}</div>')
                            onb_image = gr.Image(sources=["upload", "webcam"], type="pil",
                                                 show_label=False, elem_classes=["dk-input"])
                            onb_photo_btn = _btn(ic("image") + " " + T("Add from photo", "फ़ोटो से जोड़ें"), kind="ghost")
                with gr.Group(elem_classes=["dk-inputcard"]):
                    _panel(f'<div class="dk-inlabel">3 · {T("Khata photo (optional)", "खाता फ़ोटो (वैकल्पिक)")}</div>')
                    onb_khata_img = gr.Image(sources=["upload", "webcam"], type="pil",
                                             show_label=False, elem_classes=["dk-input"])
                    onb_khata_btn = _btn(ic("notebook") + " " + T("Read my khata", "मेरा खाता पढ़ें"), kind="ghost")
                with gr.Row(elem_classes=["dk-btnrow"]):
                    onb_to_verify = _btn(ic("magnifying-glass") + " " + T("Review", "जाँचें"), kind="primary")
                    onb_commit_btn = _btn(ic("check") + " " + T("Confirm & Save", "पक्का करें"), kind="gold")
                    onb_abort_btn = _btn(ic("x") + " " + T("Cancel", "रद्द करें"), kind="ghost")

        # ===================================================== wiring
        # Talk
        turn_out = [chat, audio_out, dash, confirm_row, composer, history]
        composer.submit(respond, [composer, history, thread_id], turn_out)

        conf_out = [chat, audio_out, dash, confirm_row, history]
        haan_btn.click(lambda tid, h: confirm("haan", tid, h),
                       [thread_id, history], conf_out)
        nahi_btn.click(lambda tid, h: confirm("nahi", tid, h),
                       [thread_id, history], conf_out)

        # per-message speaker icon → on-demand TTS into audio_out
        # (JS sets speak_idx's value+nonce, fires input+change to commit it, then
        # clicks this hidden button which reads the committed value)
        speak_btn.click(speak, [speak_idx, history], audio_out)

        # Today
        refresh_btn.click(refresh_dashboard, None, dash)
        brief_btn.click(load_briefing, None, briefing)

        # Khata — per-customer reminder; re-renders the ledger with the draft inline
        remind_btn.click(remind_one, [remind_name], khata)

        # Stock
        stock_refresh.click(refresh_stock, None, stock)

        # Receive
        rcv_parse.click(receive_parse, rcv_img, [rcv_view, preview])
        rcv_commit.click(receive_commit, preview, [rcv_view, preview, dash, stock])

        # Setup / onboarding
        begin_btn.click(onb_begin, None, onb_view)
        onb_save.click(onb_profile, [onb_owner, onb_shop, onb_lang], onb_view)
        onb_add.click(onb_manual, [onb_name, onb_qty, onb_cat], [onb_view, onb_name, onb_qty, onb_cat])
        onb_voice_btn.click(onb_voice, onb_audio, [onb_view, onb_audio])
        onb_photo_btn.click(onb_photo, onb_image, [onb_view, onb_image])
        onb_khata_btn.click(onb_khata, onb_khata_img, [onb_view, onb_khata_img])
        onb_to_verify.click(onb_verify, None, onb_view)
        onb_commit_btn.click(onb_commit, None, onb_view)
        onb_abort_btn.click(onb_abort, None, onb_view)

    return demo


# ============================================================ launch
def _warmup_async() -> None:
    """Pre-load agent graph + Whisper + TTS in the background (best-effort)."""
    import threading

    def _run() -> None:
        try:
            from dukaan import agent, stt, tts
            for fn in (agent.build_agent, stt.warmup, tts.warmup):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, daemon=True).start()


def build_head() -> str:
    """Fonts + client JS + the stylesheet, as one raw <head> blob.

    The CSS is injected here as a plain ``<style>`` rather than via ``css_paths``
    on purpose: Gradio 6 scopes css_paths under ``.gradio-container .contain``,
    which rewrites our top-level ``body.lang-hi`` toggle selectors into ones that
    can never match (``body`` lives *above* that scope) — silently breaking the
    English⇄Hindi switch. A raw <style> in <head> stays global and authoritative.
    """
    head = (ASSETS / "head.html").read_text(encoding="utf-8")
    css = (ASSETS / "style.css").read_text(encoding="utf-8")
    # Favicon as a data-URI <link> in <head> rather than launch(favicon_path=...): with
    # ssr_mode=False Gradio applies the favicon client-side and it never reaches the page,
    # whereas this head= blob is injected straight into <head>.
    fav = urllib.parse.quote((ASSETS / "favicon.svg").read_text(encoding="utf-8"))
    favicon = f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{fav}">'
    return f"{favicon}\n{head}\n<style>\n{css}\n</style>"


def main() -> None:
    _warmup_async()
    build_ui().queue().launch(
        server_name=config.GRADIO_HOST,
        server_port=config.GRADIO_PORT,
        share=config.GRADIO_SHARE,
        head=build_head(),
        theme=gr.themes.Base(),
        ssr_mode=False,
        show_error=True,
    )


if __name__ == "__main__":
    main()
