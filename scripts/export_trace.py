#!/usr/bin/env python3
"""Export a Dukaan Saathi agent trace to JSONL for the Hub (Sharing-is-Caring badge).

Runs a few real shopkeeper turns through the deepagents loop against the configured
LLM (point DUKAAN_LLM_BASE_URL at Modal) and writes every message — the human
prompt, the model's tool calls, each tool result, and the final Hindi reply — to a
JSONL trace you can upload to a Hugging Face dataset and link from the README.

Usage
-----
    uv run python scripts/export_trace.py                       # uses config endpoints
    uv run python scripts/export_trace.py https://<ws>--dukaan-llm-serve.modal.run
    # writes: logs/agent_trace.jsonl  (one JSON object per message)
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

# A short, demo-worthy script that exercises read + diagnostic + the visible
# multi-tool reasoning that makes the "Best Agent" case.
TURNS = [
    "aaj kitni bikri hui?",
    "sabse zyada udhaar kiska hai? top 3 batao",
    "Munna Yadav ka kitna udhaar baaki hai?",
    "Parle-G kyun nahi bik raha?",
]


def _route_to(base: str) -> None:
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    os.environ["DUKAAN_LLM_BASE_URL"] = base + "/v1"


def _msg_to_dict(m) -> dict:
    """Serialize a LangChain message (Human / AI / Tool) to a plain JSON dict."""
    role = getattr(m, "type", None) or getattr(m, "role", None) or m.__class__.__name__
    content = getattr(m, "content", "")
    if not isinstance(content, str):
        try:
            from dukaan.agent import _content_to_text
            content = _content_to_text(content)
        except Exception:
            content = str(content)
    out: dict = {"role": role, "content": content}
    tcs = getattr(m, "tool_calls", None) or []
    if tcs:
        out["tool_calls"] = [
            {"name": (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)),
             "args": (tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None))}
            for tc in tcs
        ]
    name = getattr(m, "name", None)
    if name:
        out["name"] = name   # tool name on a ToolMessage
    return out


def main() -> int:
    if len(sys.argv) > 1:
        _route_to(sys.argv[1])

    from dukaan import agent, config

    out_path = Path("logs/agent_trace.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting agent trace via {config.LLM_BASE_URL}")
    thread = uuid.uuid4().hex
    records: list[dict] = []
    seen = 0
    for turn in TURNS:
        print(f"  turn: {turn!r}")
        res = agent.run_agent(turn, thread_id=thread)
        if res.get("error"):
            print(f"    !! error: {res['error']}")
        msgs = res.get("messages", [])
        for m in msgs[seen:]:           # only this turn's new messages (history accumulates)
            records.append({"turn": turn, **_msg_to_dict(m)})
        seen = len(msgs)
        print(f"    tools: {res.get('tool_calls')}  intent: {res.get('intent')}")

    with open(out_path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(records)} messages -> {out_path}")
    print("Upload it to a Hub dataset/repo and link it from the README for the Sharing-is-Caring badge.")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
