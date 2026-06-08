#!/usr/bin/env python
"""Build the polished Final Report PDF from final_report.md.

Same toolchain as docs/revision/build_revision_pdf.py: markdown -> styled HTML ->
WeasyPrint, Mermaid blocks -> PNG via kroki.io, Devanagari via an embedded Noto
Sans Devanagari variable font, emoji sanitized so nothing prints as tofu.
Run: uv run --with weasyprint --with markdown python docs/report/build_final_report.py
"""
import base64
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "final_report.md"
ASSETS = ROOT / "docs" / "report" / "assets"
FONT = ROOT / "docs" / "revision" / "assets" / "NotoSansDevanagari-VF.ttf"
OUT = ROOT / "docs" / "Dukaan_Saathi_Final_Report.pdf"
DATE = "2026-06-07"

CAPTIONS = [
    "Figure 1 — Revised architecture: one shopkeeper turn through the UI-agnostic seam",
    "Figure 2 — Data model: FEFO lots under the merged inventory row (two SQLite DBs)",
    "Figure 3 — Voice udhaar with confirm-before-write (nothing writes until 'haan')",
    "Figure 4 — Challan photo to auto-receive (vision -> stage -> commit via FEFO lots)",
    "Figure 5 — Real-owner onboarding state machine (demo -> real)",
]


# ---------------------------------------------------------------- emoji cleanup
def sanitize(s: str) -> str:
    s = s.replace("✅", "✓")
    s = s.replace("⌨", "")
    s = re.sub(r"[\U0001F300-\U0001FAFF️☀-⛿]", "", s)  # pictographs/misc-symbols
    return s


# ---------------------------------------------------------------- kroki render
def kroki_png(diagram: str) -> bytes:
    req = urllib.request.Request(
        "https://kroki.io/mermaid/png",
        data=sanitize(diagram).encode("utf-8"),
        headers={
            "Content-Type": "text/plain",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) DukaanPDF/1.0",
            "Accept": "image/png",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    text = SRC.read_text(encoding="utf-8")

    # 1) pull mermaid blocks out, leave a token
    diagrams: list[str] = []

    def _grab(m):
        diagrams.append(m.group(1))
        return f"\n\nDUKAANDIAGRAM{len(diagrams) - 1}\n\n"

    text = re.sub(r"```mermaid\n(.*?)\n```", _grab, text, flags=re.S)

    # 2) drop the manual TOC (the PDF gets an auto TOC with page numbers)
    text = re.sub(r"## Table of contents.*?\n---\n", "", text, count=1, flags=re.S)
    # 3) drop the leading H1 (title lives on the cover); strip standalone <hr>
    text = re.sub(r"^# .*\n", "", text, count=1)
    text = re.sub(r"(?m)^---\s*$\n?", "", text)
    text = sanitize(text)

    # 4) render diagrams -> data-uri figures
    figs = []
    for i, d in enumerate(diagrams):
        cap = CAPTIONS[i] if i < len(CAPTIONS) else ""
        try:
            png = kroki_png(d)
            (ASSETS / f"diagram_{i}.png").write_bytes(png)
            uri = "data:image/png;base64," + base64.b64encode(png).decode()
            figs.append(
                f'<figure class="diagram"><img src="{uri}"/><figcaption>{cap}</figcaption></figure>'
            )
            print(f"  diagram {i}: {len(png) // 1024} KB")
        except Exception as e:  # graceful fallback to source
            print(f"  diagram {i}: kroki FAILED ({e}) — embedding source", file=sys.stderr)
            figs.append(
                f'<figure class="diagram"><pre class="mmsrc">{d}</pre><figcaption>{cap}</figcaption></figure>'
            )

    # 5) markdown -> html
    import markdown

    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "attr_list", "sane_lists"],
        extension_configs={"toc": {"toc_depth": 2}},
    )
    body = md.convert(text)
    toc_html = md.toc
    for i in range(len(diagrams)):
        body = body.replace(f"<p>DUKAANDIAGRAM{i}</p>", figs[i])

    # 6) assemble + render
    cover = f"""
    <section class="cover">
      <div class="pad">
        <div class="kicker">Final Report &middot; As-Built After the Track-1 Revision</div>
        <h1>Dukaan Saathi</h1>
        <div class="sub">Hindi-first voice + photo assistant for a kirana shop's inventory &amp; udhaar ledger</div>
        <div class="pitch">The complete end-to-end picture after the revision: every component that changed,
        how a shopkeeper's turn flows through the stack today, and the proof it works &mdash;
        <b>59 headless tests</b> + a <b>9/9 real-model GPU end-to-end</b>. Backend only; the Gradio UI
        &amp; Hugging&nbsp;Face Space build on the <code>session.py</code> seam.</div>
        <div class="chips">
          <span class="chip">FEFO batch &amp; expiry</span>
          <span class="chip">Real-owner onboarding</span>
          <span class="chip">Confirm-before-write</span>
          <span class="chip">Multilingual STT + Hindi 2nd pass</span>
          <span class="chip">Challan auto-receive</span>
          <span class="chip">59 tests &middot; 9/9 GPU e2e</span>
        </div>
        <div class="meta">{DATE} &nbsp;&middot;&nbsp; as-built reference &nbsp;&middot;&nbsp; backend complete &amp; verified</div>
      </div>
    </section>
    <section class="toc-page">
      <h1 class="toc-h">Contents</h1>
      {toc_html}
    </section>
    """
    html = (
        f"<!doctype html><html><head><meta charset='utf-8'><style>{css()}</style></head>"
        f"<body>{cover}{body}</body></html>"
    )

    from weasyprint import HTML

    doc = HTML(string=html, base_url=str(ROOT)).render()
    doc.write_pdf(str(OUT))
    print(f"\nwrote {OUT}  ({OUT.stat().st_size // 1024} KB, {len(doc.pages)} pages)")
    return 0


def css() -> str:
    return f"""
:root{{
  --ink:#1f2933; --muted:#5b6b7b; --line:#e4e9f0;
  --brand:#cf5a1c; --brand-dark:#a8470f; --brand2:#18756a;
  --brand-tint:#fdf2ea; --teal-tint:#e9f5f3; --code-bg:#f6f8fb;
}}
@font-face {{ font-family:"Noto Sans Devanagari"; src:url("file://{FONT}"); font-weight:100 900; }}

@page {{ size:A4; margin:16mm 15mm 17mm 15mm;
  @bottom-left{{ content:"Dukaan Saathi \\00b7 final report"; font-size:8pt; color:#9aa5b1; }}
  @bottom-right{{ content:counter(page)" / "counter(pages); font-size:8pt; color:#9aa5b1; }}
}}
@page cover {{ margin:0; @bottom-left{{content:none}} @bottom-right{{content:none}} }}

*{{ box-sizing:border-box; }}
html{{ font-size:10.2pt; }}
body{{ font-family:"DejaVu Sans","Noto Sans Devanagari",sans-serif; color:var(--ink); line-height:1.5; margin:0; }}

/* cover */
.cover{{ page:cover; height:297mm; position:relative; color:#fff;
  background:linear-gradient(150deg,#cf5a1c 0%,#b8480f 42%,#18756a 100%); }}
.cover .pad{{ padding:42mm 26mm; }}
.cover .kicker{{ font-size:11pt; letter-spacing:3px; text-transform:uppercase; opacity:.9; }}
.cover h1{{ font-size:50pt; margin:7mm 0 3mm; line-height:1.02; font-weight:800; }}
.cover .sub{{ font-size:15pt; opacity:.97; max-width:150mm; }}
.cover .pitch{{ margin-top:11mm; font-size:11.5pt; opacity:.95; max-width:152mm; line-height:1.6; }}
.cover .pitch code{{ background:rgba(255,255,255,.16); color:#fff; padding:0.5px 5px; border-radius:3px; }}
.cover .chips{{ margin-top:11mm; }}
.cover .chip{{ display:inline-block; border:1px solid rgba(255,255,255,.55); border-radius:20px;
  padding:3px 11px; font-size:8.8pt; margin:0 5px 6px 0; }}
.cover .meta{{ position:absolute; left:26mm; bottom:24mm; font-size:9.6pt; opacity:.95; }}

/* toc */
.toc-page{{ break-after:page; }}
.toc-h{{ font-size:22pt; color:var(--brand); margin:2mm 0 6mm; }}
.toc ul{{ list-style:none; padding:0; margin:0; }}
.toc li{{ margin:2.6mm 0; font-size:10.6pt; }}
.toc a{{ display:flex; justify-content:space-between; align-items:flex-end; gap:3mm;
  text-decoration:none; color:var(--ink); border-bottom:1px dotted #c7d0da; padding-bottom:1.2mm; }}
.toc a::after{{ content:target-counter(attr(href),page); color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }}

/* headings */
h2{{ font-size:18pt; color:var(--brand-dark); margin:0 0 4mm; padding-bottom:2mm;
  border-bottom:3px solid var(--brand); break-before:page; break-after:avoid; }}
h2:first-of-type{{ break-before:avoid; }}
h3{{ font-size:12.5pt; color:var(--brand2); margin:6mm 0 2mm; break-after:avoid; }}
h4{{ font-size:10.6pt; color:var(--ink); margin:4mm 0 1.5mm; break-after:avoid; }}
p{{ margin:0 0 2.6mm; }}
ul,ol{{ margin:0 0 3mm; padding-left:6mm; }}
li{{ margin:1.2mm 0; }}
a{{ color:var(--brand2); }}
strong{{ color:#14202b; }}
em{{ color:#33414e; }}

/* code */
code{{ font-family:"DejaVu Sans Mono",monospace; font-size:8.5pt; background:var(--code-bg);
  padding:0.5px 4px; border-radius:3px; color:#b4380e; }}
pre{{ background:var(--code-bg); border:1px solid #e3e9f1; border-left:3px solid var(--brand2);
  border-radius:5px; padding:3mm 4mm; font-family:"DejaVu Sans Mono",monospace; font-size:8pt;
  line-height:1.4; white-space:pre-wrap; word-break:break-word; margin:3mm 0; break-inside:avoid; color:#243; }}
pre code{{ background:none; color:inherit; padding:0; font-size:inherit; }}
pre.mmsrc{{ color:#556; }}

/* blockquotes -> callout */
blockquote{{ border-left:4px solid var(--brand); background:var(--brand-tint); margin:3.5mm 0;
  padding:3mm 4mm; border-radius:0 6px 6px 0; break-inside:avoid; font-size:9.8pt; color:#3a2a20; }}
blockquote p{{ margin:0 0 1.5mm; }} blockquote p:last-child{{ margin:0; }}

/* tables */
table{{ width:100%; border-collapse:collapse; margin:3mm 0; font-size:8.5pt; break-inside:auto; }}
thead{{ display:table-header-group; }}
th{{ background:#233140; color:#fff; text-align:left; padding:2.2mm 2.6mm; font-weight:600; vertical-align:top; }}
td{{ padding:1.9mm 2.6mm; border-bottom:1px solid var(--line); vertical-align:top; }}
tbody tr:nth-child(even) td{{ background:#f8fafc; }}
tr,td,th{{ break-inside:avoid; }}

/* figures */
figure.diagram{{ margin:5mm 0; text-align:center; break-inside:avoid;
  border:1px solid var(--line); border-radius:8px; padding:4mm 3mm 2mm; background:#fff; }}
figure.diagram img{{ max-width:100%; max-height:200mm; height:auto; }}
figcaption{{ font-size:8.6pt; color:var(--muted); margin-top:2mm; font-style:italic; }}

hr{{ border:none; border-top:1px solid var(--line); margin:5mm 0; }}
"""


if __name__ == "__main__":
    raise SystemExit(main())
