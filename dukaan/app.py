"""Gradio single-screen app for Dukaan Saathi.

One clean Hindi-first screen that ties the whole stack together: the shopkeeper
speaks (or types, or snaps a photo of a bill/label), the deepagents loop over
Gemma-4 figures out the intent and calls a tool / queries the DB, and the reply
is shown in the chat *and* spoken back in Hindi. A live "aaj ka hisaab"
dashboard on the right summarises stock value, today's sales, expiry/low-stock
alerts, pending udhaar, and the next festival nudge.

Heavy models (Whisper, MMS/Parler TTS, the agent graph) are loaded lazily on
first use inside ``stt`` / ``tts`` / ``agent`` — nothing ML-related is touched at
import time, so this module imports instantly and ``llama-server`` need not be up.
"""

from __future__ import annotations

import uuid

import gradio as gr
import numpy as np

from dukaan import agent, config, llm, ops, proactive, session, stt, tts

# --------------------------------------------------------------------------- text
TAGLINE = "बोलिए या बिल दिखाइए — स्टॉक, उधार और हिसाब सब अपने आप।"

_PLACEHOLDER = (
    "जैसे: '10 Parle-G packet aaye, 5 rupaye wala' · "
    "'Sharma ji ne 200 ka udhaar liya' · 'aaj kitni bikri hui?'"
)

# A friendly Hindi fallback shown in the chat when anything below blows up.
_ERROR_REPLY = "माफ़ कीजिए, कुछ गड़बड़ हो गई। थोड़ी देर बाद फिर कोशिश करें।"


def _money(x: float | int | None) -> str:
    """Format a number as ₹ with the project currency symbol."""
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return f"{config.CURRENCY}0"
    return f"{config.CURRENCY}{v:,.0f}" if v.is_integer() else f"{config.CURRENCY}{v:,.2f}"


# --------------------------------------------------------------------- dashboard
def dashboard_md() -> str:
    """Render the live "aaj ka hisaab" panel as Markdown (₹ + emojis).

    Combines :func:`ops.dashboard_snapshot` (stock value, today's sales, expiring,
    low stock, udhaar) with :func:`proactive.run_all` (festival nudge). Never
    raises — on any failure it returns a short Hindi error line instead.
    """
    lines: list[str] = []
    if not llm.health():
        lines.append(
            "> ⚠️ **Gemma (llama-server) abhi up nahi** — "
            "`scripts/serve_llm.sh` chalaayein.\n"
        )

    try:
        snap = ops.dashboard_snapshot()
    except Exception as exc:  # DB not seeded / locked etc.
        lines.append(f"⚠️ हिसाब नहीं मिला: `{exc}`")
        return "\n".join(lines)

    # ---- stock value ----
    sv = snap.get("stock_value", {}) or {}
    lines.append("### 💰 स्टॉक की कीमत")
    lines.append(
        f"- लागत पर **{_money(sv.get('at_cost'))}** → MRP पर **{_money(sv.get('at_mrp'))}**"
    )
    lines.append(
        f"- संभावित मुनाफ़ा **{_money(sv.get('potential_margin'))}** · "
        f"{sv.get('total_units', 0)} units · {sv.get('item_count', 0)} items"
    )

    # ---- today ----
    td = snap.get("today", {}) or {}
    lines.append("\n### 🛒 आज की बिक्री")
    lines.append(
        f"- बिक्री **{_money(td.get('revenue'))}** · {td.get('units', 0)} units "
        f"· {td.get('num_sales', 0)} sales"
    )
    top = td.get("top_items") or []
    if top:
        names = ", ".join(
            f"{t.get('name')} ({t.get('qty', 0)})" for t in top[:3]
        )
        lines.append(f"- टॉप: {names}")

    # ---- expiring soon ----
    exp = snap.get("expiring") or []
    lines.append(f"\n### ⏳ जल्दी एक्सपायर ({len(exp)})")
    if exp:
        for e in exp[:5]:
            dl = e.get("days_left")
            when = f"{dl} din" if dl is not None and dl >= 0 else "बीत चुकी"
            lines.append(
                f"- {e.get('name')} — {e.get('qty', 0)} pcs · {when} ({e.get('expiry_date')})"
            )
    else:
        lines.append("- सब ठीक है ✅")

    # ---- low stock ----
    low = snap.get("low_stock") or []
    lines.append(f"\n### 📉 कम स्टॉक ({len(low)})")
    if low:
        lines.append(
            "- " + ", ".join(f"{l.get('name')} ({l.get('qty', 0)})" for l in low[:6])
        )
    else:
        lines.append("- कोई item कम नहीं ✅")

    # ---- udhaar ----
    ud = snap.get("udhaar", {}) or {}
    custs = ud.get("customers") or []
    lines.append(
        f"\n### 📒 बाकी उधार — {_money(ud.get('total'))} ({ud.get('count', 0)} ग्राहक)"
    )
    if custs:
        for c in custs[:6]:
            flag = " 🔴" if c.get("overdue") else ""
            lines.append(f"- {c.get('name')}: **{_money(c.get('balance'))}**{flag}")
    else:
        lines.append("- कोई उधार बाकी नहीं ✅")

    # ---- festival nudge ----  call festival_nudge() directly (NOT run_all(),
    # which would also LLM-draft udhaar reminders) so dashboard refresh stays cheap.
    try:
        fest = proactive.festival_nudge() or {}
        msg = fest.get("message")
        if msg:
            lines.append("\n### 🪔 आने वाला त्योहार")
            lines.append(f"- {msg}")
    except Exception:
        pass  # festival nudge is best-effort; never break the dashboard

    return "\n".join(lines)


def alerts_md() -> str:
    """On-demand proactive panel: expiry warnings, overdue-udhaar WhatsApp drafts,
    and the festival nudge. Triggered by a button (the udhaar drafts make LLM
    calls, so we don't run them on every dashboard refresh)."""
    try:
        a = proactive.run_all()
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ अलर्ट नहीं मिले: `{exc}`"

    out: list[str] = ["### ⏳ एक्सपायरी", (a.get("expiry") or {}).get("message", "—")]
    ud = a.get("udhaar") or {}
    out.append("\n### 🔔 उधार reminder (WhatsApp draft)")
    out.append(ud.get("message", "—"))
    for r in (ud.get("reminders") or [])[:5]:
        phone = f" · 📱 {r['phone']}" if r.get("phone") else ""
        out.append(f"\n**{r.get('customer')}** ({_money(r.get('balance'))}){phone}")
        out.append(f"> {r.get('draft')}")
    fest = a.get("festival") or {}
    out.append("\n### 🪔 त्योहार")
    out.append(fest.get("message", "—"))
    return "\n".join(out)


# ----------------------------------------------------------------------- respond
def respond(
    audio: tuple[int, np.ndarray] | None,
    image: object | None,
    text: str | None,
    tts_on: bool,
    history: list[dict],
    thread_id: str,
):
    """Handle one user turn (voice / image / text) and return UI updates.

    Returns ``(history, audio_out, dashboard_md, audio_in_reset, image_reset,
    text_reset)`` so Gradio can append the exchange, optionally speak the reply,
    refresh the dashboard, and clear the three inputs.
    """
    history = list(history or [])
    try:
        # Thin adapter over the UI-agnostic seam: it transcribes / OCRs / runs the
        # agent / synthesises TTS and hands back a structured TurnResult; we just
        # append the exchange to the chat and refresh the dashboard.
        r = session.handle_turn(audio=audio, text=text, image=image, thread_id=thread_id, tts=tts_on)
        history.append({"role": "user", "content": r.user_text or "🎤/🖼️"})
        history.append({"role": "assistant", "content": r.reply_text})
        return history, r.reply_audio, dashboard_md(), None, None, ""

    except Exception as exc:  # noqa: BLE001 — never crash the UI
        history.append({"role": "user", "content": (text or "").strip() or "🎤/🖼️"})
        history.append({"role": "assistant", "content": f"{_ERROR_REPLY}\n`{exc}`"})
        # Try to still refresh the dashboard; fall back to empty if that fails too.
        try:
            dash = dashboard_md()
        except Exception:
            dash = ""
        return history, None, dash, None, None, ""


# --------------------------------------------------------------------------- UI
def build_ui() -> gr.Blocks:
    """Construct the single-screen Gradio app (no models loaded here)."""
    # NB: gradio 6.x moved ``theme`` from the Blocks() ctor to ``launch()`` (see main()).
    with gr.Blocks(title="दुकान साथी · Dukaan Saathi") as demo:
        thread_id = gr.State(lambda: uuid.uuid4().hex)

        gr.Markdown("# 🏪 दुकान साथी · Dukaan Saathi")
        gr.Markdown(f"_{TAGLINE}_")

        with gr.Row():
            # ---------------------------------------------------------- LEFT
            with gr.Column(scale=3):
                # NB: gradio 6.x dropped the explicit ``type="messages"`` kwarg —
                # the {"role","content"} message format (what ``respond`` emits) is
                # now the only supported data model, so no ``type=`` is passed.
                chatbot = gr.Chatbot(
                    height=440,
                    label="बातचीत",
                    avatar_images=(None, "🏪"),
                )
                audio_in = gr.Audio(
                    sources=["microphone", "upload"],
                    type="numpy",
                    label="🎤 बोलिए",
                )
                image_in = gr.Image(
                    sources=["upload", "webcam"],
                    type="pil",
                    label="🖼️ बिल/लेबल",
                )
                text_in = gr.Textbox(
                    label="⌨️ टाइप करें",
                    placeholder=_PLACEHOLDER,
                    lines=2,
                )
                with gr.Row():
                    send_btn = gr.Button("भेजें · Send", variant="primary", scale=3)
                    tts_on = gr.Checkbox(value=True, label="🔊 आवाज़ में जवाब", scale=2)
                audio_out = gr.Audio(label="जवाब", autoplay=True)

            # --------------------------------------------------------- RIGHT
            with gr.Column(scale=2):
                gr.Markdown("## 📊 आज का हिसाब")
                dashboard = gr.Markdown(dashboard_md)
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh", scale=1)
                    alerts_btn = gr.Button("🔔 अलर्ट · Reminders", scale=1)
                alerts_box = gr.Markdown("")

        # ----------------------------------------------------------- wiring
        outputs = [chatbot, audio_out, dashboard, audio_in, image_in, text_in]
        inputs = [audio_in, image_in, text_in, tts_on, chatbot, thread_id]

        send_btn.click(respond, inputs=inputs, outputs=outputs)
        text_in.submit(respond, inputs=inputs, outputs=outputs)
        refresh_btn.click(dashboard_md, inputs=None, outputs=dashboard)
        alerts_btn.click(alerts_md, inputs=None, outputs=alerts_box)

    return demo


def _warmup_async() -> None:
    """Pre-load the agent graph + Whisper + TTS in a background thread, so the UI
    is available immediately while the first real interaction stays fast."""
    import threading

    def _run() -> None:
        for fn in (agent.build_agent, stt.warmup, tts.warmup):
            try:
                fn()
            except Exception:  # noqa: BLE001 — warmup is best-effort
                pass

    threading.Thread(target=_run, daemon=True).start()


def main() -> None:
    """Launch the app on the configured host/port."""
    _warmup_async()
    build_ui().queue().launch(
        server_name=config.GRADIO_HOST,
        server_port=config.GRADIO_PORT,
        share=config.GRADIO_SHARE,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()
