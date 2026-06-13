"""The deepagents agent — the agentic core of Dukaan Saathi.

A Hindi-first "Dukaan Saathi" for a kirana shop owner who speaks Hindi/Hinglish.
The agent decides, per turn, whether the shopkeeper is *recording a change*
(call a WRITE tool), *asking a lookup* (use a read tool / text-to-SQL), or asking
a *diagnostic* "why" question (investigate over several reads, then reason).

Built on deepagents v0.6.8 with our local Gemma-4-12B (llama.cpp, OpenAI-compatible)
passed as a ``ChatOpenAI`` **instance** (never an ``"openai:..."`` string, which would
route to the Responses API that llama.cpp does not implement). Multi-turn memory
comes from an ``InMemorySaver`` keyed by a stable ``thread_id``. No subagents
(deepagents 0.6.8 has an open recursion bug with them).

Nothing here contacts the server at import time — the model and graph are built
lazily inside :func:`build_agent` (cached) and used by :func:`run_agent`.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

from dukaan import config, db, llm, staging, tools

# --------------------------------------------------------------------------- prompt
_PROMPT_BODY = """\
Tum "Dukaan Saathi" ho — ek Indian kirana (general store) dukaandar ka samajhdar
sahayak. Dukaandar tumse Hindi ya Hinglish me baat karta hai (bolkar ya likhkar).
Tumhara kaam hai uska maal (inventory), bikri (sales), aur udhaar (credit) ka
hisaab sambhalna aur uske sawaalon ka seedha jawaab dena.

Har baat ka sahi roop pehchaano aur uske hisaab se kaam karo:

1) BADLAAV DARJ KARNA (record karna) — jab dukaandar kuch hua bata raha ho:
   - naya stock / maal aaya, restock  -> add_inventory_tool YA record_purchase_tool
     (agar supplier/lागत/cost ka zikr ho to record_purchase_tool).
   - kuch becha / bika / sale hui      -> record_sale_tool.
   - kisi ne udhaar liya / udhaar diya -> add_udhaar_tool.
   - kisi ne paise jama kiye / diye     -> record_payment_tool.
   Sahi tool ko zaroori jaankari (item, quantity, daam, customer ka naam) ke saath
   TURANT call karo — khud se "likh du?" mat poochho. Ye write tools seedhe likhte
   nahi, pehle stage karke "haan/nahi" maangte hain; tool jo confirmation line lautaaye
   use jyon ka tyon Hindi me bata do.

2) SAWAAL / LOOKUP — jab dukaandar kuch poochh raha ho (kitna, kaun, total, aaj/kal
   ki bikri, kiska kitna udhaar, kis cheez ka kya haal):
   - poori dukaan ka snapshot / aaj ki bikri / stock value / expire hone wale / kam
     stock / kul udhaar  -> get_dashboard.
   - kisi ek customer ka udhaar / baaki paise  -> get_customer_dues.
   - kisi ek item ki poori detail (stock, daam, margin, bikri ka trend)  -> get_item_detail.
   - baaki kisi bhi tarah ke aankde ke liye ek SQL SELECT likho aur query_database
     ko do (neeche diya schema istemaal karo). Sirf SELECT / WITH chalega.

3) "KYUN" / DIAGNOSTIC SAWAAL — jaise "X kyun nahi bik raha?", "is hafte kam bikri
   kyun?": ek hi jawaab me mat ruko. Kai baar query_database / get_item_detail call
   karke jaanch karo — bikri ka trend dekho, stock aur expiry dekho, doosre milte-julte
   items se tulna karo — phir wajah samajh kar saral Hindi me samjhaao aur ek chhota
   sujhaav do (jaise daam, expiry, ya promotion).

Jawaab dene ke niyam:
- BHASHA (reply language) — SABSE ZAROORI: har user-message ke shuru me ek control tag
  aata hai: "[reply:en]" ya "[reply:hi]". Usi tag ke hisaab se jawaab ki bhasha chuno:
    • [reply:hi]  -> sirf Devanagari (हिंदी) lipi me likho — roman/English akshar me Hindi
      MAT likho. (jaise: "आज की कुल बिक्री ₹528 रही, सबसे ज़्यादा पारले-जी बिका।")
    • [reply:en]  -> sirf saaf, saral English me jawaab do.
  Koi tag na ho to default English. Is tag ko jawaab me KABHI mat dikhao, na hi iska zikr karo.
- Chhota aur baat-cheet wale andaaz me jawaab do, jaise dukaandar se aamne-saamne baat
  kar rahe ho. Markdown (*, #) ka kam se kam istemaal karo — saadi Hindi vakya likho.
- Paison ke aankde theek-theek {currency} ke saath batao (jaise {currency}1,250).
- SQL, tools, database, ya apne "steps" ka zikr KABHI mat karo — sirf seedha jawaab do.
- File wale tools (ls / read_file / write_file / edit_file) KABHI istemaal mat karo —
  ye dukaan ka kaam nahi hai, inse door raho.
- Agar koi cheez inventory ya khaate me na mile, to vinamrata se Hindi me bata do.

CONFIRM ka niyam — BAHUT ZAROORI: WRITE tools (add_inventory_tool, record_sale_tool,
record_purchase_tool, add_udhaar_tool, record_payment_tool) database me TURANT nahi
likhte — woh badlaav ko stage karke KHUD ek "...likh du? (haan/nahi)" line lautaate
hain. Isliye jab dukaandar koi badlaav bataye, us write tool ko FAURAN call karo —
call karna safe hai, isse kuch likha nahi jaata. Tool ki lautaai "haan/nahi" line ko
hi apna jawaab banao. KHUD se pehle "likh du?" MAT poochho aur bina tool call kiye
confirmation mat maango. Jab dukaandar agle turn me "haan" (theek/sahi/kar do) ya
"nahi" bole, tab confirm_pending_tool ko us jawaab ke saath call karo — asli likhai
sirf tabhi hoti hai.

Aaj ki taareekh ka hisaab database ke date('now') / datetime('now','localtime') se
lagao. Niche database ka schema diya hai — isi ke hisaab se SELECT likhna:

{schema}
"""

#: System prompt prepended to the deepagents base prompt. Built once at import
#: (pure string formatting — no network).
DUKAAN_SYSTEM_PROMPT: str = _PROMPT_BODY.format(
    currency=config.CURRENCY,
    schema=db.SCHEMA_DESCRIPTION,
)


# --------------------------------------------------------------------------- agent
_AGENT: Any | None = None  # cached compiled graph


def build_agent():
    """Build (and cache) the compiled deepagents graph.

    Uses our local Gemma-4 ``ChatOpenAI`` instance, the Dukaan tool registry, the
    Hindi system prompt, and an in-memory checkpointer for multi-turn memory.
    No subagents (deepagents 0.6.8 recursion bug). Built lazily — calling this is
    the first thing that constructs the model; it still does not hit the network
    until the graph is actually invoked.
    """
    global _AGENT
    if _AGENT is None:
        model = llm.make_chat_model()
        _AGENT = create_deep_agent(
            model=model,
            tools=tools.TOOLS,
            system_prompt=DUKAAN_SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )
    return _AGENT


def _content_to_text(content: Any) -> str:
    """Flatten a message ``content`` (str or list of content blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # text blocks look like {"type": "text", "text": "..."}
                txt = block.get("text") or block.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts).strip()
    return str(content) if content is not None else ""


def _collect_tool_calls(messages: list) -> list[str]:
    """Return tool-call names made in the CURRENT turn only.

    With a checkpointer, ``messages`` holds the full conversation history, so we
    scan only the messages produced after the last human turn.
    """
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        role = getattr(messages[i], "type", None) or getattr(messages[i], "role", None)
        if role in ("human", "user"):
            start = i + 1
            break
    names: list[str] = []
    for msg in messages[start:]:
        for call in (getattr(msg, "tool_calls", None) or []):
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                names.append(name)
    return names


def _lang_prefix(reply_lang: str) -> str:
    """Per-turn reply-language control tag the system prompt obeys.

    The UI toggle (default English) sets ``reply_lang``; we prepend ``[reply:en]``
    or ``[reply:hi]`` to the user message so the model's reply language follows the
    toggle on every turn (and switches mid-chat) without rebuilding the agent.
    """
    return "[reply:hi] " if str(reply_lang or "").lower().startswith("hi") else "[reply:en] "


def run_agent(user_text: str, thread_id: str = "default", reply_lang: str = "en") -> dict:
    """Run one agent turn for ``user_text`` and return a structured result.

    Returns ``{"reply", "messages", "tool_calls", "intent", "pending"}`` on
    success — ``intent`` is the heuristic badge derived from this turn's tool
    calls and ``pending`` is the thread's staged write batch (or ``None``). On any
    failure (server down, ``GraphRecursionError``, etc.) returns a polite Hindi
    apology plus ``"error"`` with the exception text — never raises.
    """
    # Bind the staging thread BEFORE invoke so write tools stage into the right
    # batch (the ContextVar carries across LangGraph's async boundaries).
    staging.bind_thread(thread_id)
    try:
        agent = build_agent()
        result = agent.invoke(
            {"messages": [{"role": "user", "content": _lang_prefix(reply_lang) + user_text}]},
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": config.AGENT_RECURSION_LIMIT,
            },
        )
        messages = result.get("messages", [])
        reply = _content_to_text(messages[-1].content) if messages else ""
        tool_calls = _collect_tool_calls(messages)
        return {
            "reply": reply,
            "messages": messages,
            "tool_calls": tool_calls,
            "intent": _intent_from_tool_calls(tool_calls),
            "pending": staging.get_pending(thread_id),
        }
    except Exception as e:  # noqa: BLE001 — surface any failure as a graceful reply
        return {
            "reply": "Maaf kijiye, abhi javaab dene me dikkat aa rahi hai. "
                     "Kripya thodi der baad dobara poochhiye.",
            "messages": [],
            "tool_calls": [],
            "intent": "chat",
            "pending": None,
            "error": str(e),
        }


def stream_agent(user_text: str, thread_id: str = "default", reply_lang: str = "en"):
    """Stream one agent turn, token by token.

    Yields ``("delta", accumulated_reply)`` as the assistant's final answer is
    generated, then ``("final", {...})`` with the same shape as
    :func:`run_agent`'s result (reply / messages / tool_calls / intent / pending /
    error). Only assistant text (``AIMessageChunk``) is streamed — tool outputs
    are filtered out. Never raises; any failure surfaces in the final payload.
    """
    staging.bind_thread(thread_id)
    cfg = {"configurable": {"thread_id": thread_id},
           "recursion_limit": config.AGENT_RECURSION_LIMIT}
    acc = ""
    try:
        agent = build_agent()
        last_status = ""
        for chunk, _meta in agent.stream(
            {"messages": [{"role": "user", "content": _lang_prefix(reply_lang) + user_text}]},
            config=cfg, stream_mode="messages",
        ):
            cls = chunk.__class__.__name__
            if cls == "AIMessageChunk":
                # a tool call is being formed -> tell the UI we're hitting the DB
                for tc in (getattr(chunk, "tool_call_chunks", None) or []):
                    nm = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                    if nm:
                        code = "write" if nm in _WRITE_TOOLS else "read"
                        if code != last_status:
                            last_status = code
                            yield "status", code
                piece = _content_to_text(getattr(chunk, "content", ""))
                if piece:
                    acc += piece
                    yield "delta", acc
            elif cls == "ToolMessage":
                nm = getattr(chunk, "name", None)
                if nm:
                    code = "write" if nm in _WRITE_TOOLS else "read"
                    if code != last_status:
                        last_status = code
                        yield "status", code
        # authoritative final state (for tool_calls + the committed reply)
        try:
            messages = agent.get_state(cfg).values.get("messages", [])
        except Exception:  # noqa: BLE001
            messages = []
        reply = (_content_to_text(messages[-1].content) if messages else acc).strip() or acc.strip()
        tool_calls = _collect_tool_calls(messages)
        yield "final", {
            "reply": reply,
            "messages": messages,
            "tool_calls": tool_calls,
            "intent": _intent_from_tool_calls(tool_calls),
            "pending": staging.get_pending(thread_id),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 — surface as a graceful final payload
        yield "final", {
            "reply": acc.strip() or "Maaf kijiye, abhi javaab dene me dikkat aa rahi hai. "
                                    "Kripya thodi der baad dobara poochhiye.",
            "messages": [], "tool_calls": [], "intent": "chat",
            "pending": staging.get_pending(thread_id), "error": str(e),
        }


# --------------------------------------------------------------------- intent badge
_INTENT_CHOICES = {"write", "lookup", "diagnostic", "reminder", "chat"}

#: Tool names that mean the owner recorded a change (or confirmed a staged one).
_WRITE_TOOLS = {
    "add_inventory_tool",
    "record_sale_tool",
    "record_purchase_tool",
    "add_udhaar_tool",
    "record_payment_tool",
    "confirm_pending_tool",
}
#: Tool names that mean the owner asked for a fact / number.
_LOOKUP_TOOLS = {
    "query_database",
    "get_dashboard",
    "get_item_detail",
    "get_customer_dues",
}


def _intent_from_tool_calls(tool_calls: list[str]) -> str:
    """Map a turn's tool-call names to an intent badge — pure, zero LLM calls.

    ``write`` if any write/confirm tool ran, else ``lookup`` if any read tool ran,
    else ``chat``. This is the authoritative badge: it reflects what the agent
    actually *did*, not what the text seemed to ask for.
    """
    names = set(tool_calls or [])
    if names & _WRITE_TOOLS:
        return "write"
    if names & _LOOKUP_TOOLS:
        return "lookup"
    return "chat"


def classify_intent(text: str = "", tool_calls: list[str] | None = None) -> str:
    """Best-effort one-word intent label for a UI badge — no LLM, never raises.

    When ``tool_calls`` is given, defer to :func:`_intent_from_tool_calls` (the
    accurate, action-based signal). Otherwise fall back to a cheap keyword
    heuristic over the Hindi/Hinglish ``text`` for a pre-run guess. Returns one of
    write/lookup/diagnostic/chat; defaults to ``"chat"``.
    """
    if tool_calls is not None:
        return _intent_from_tool_calls(tool_calls)

    t = (text or "").lower()
    # "kyun" (why) signals a diagnostic question; check it before lookup so a
    # "X kyun nahi bika" doesn't get swallowed by the "bika" write keyword.
    if "kyun" in t or "kyon" in t:
        return "diagnostic"
    if any(k in t for k in ("bika", "becha", "bech", "sale", "udhaar", "khaata", "khata", "jama")):
        return "write"
    if any(k in t for k in ("kitna", "kitni", "kaun", "total", "bikri", "stock", "baaki", "haal")):
        return "lookup"
    return "chat"
